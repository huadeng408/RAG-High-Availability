// Package service contains business logic.
package service

import (
	"context"
	"encoding/json"
	"fmt"
	"sort"
	"strings"
	"time"

	"pai-smart-go/internal/config"
	"pai-smart-go/internal/model"
	"pai-smart-go/internal/repository"
	"pai-smart-go/pkg/llm"
	"pai-smart-go/pkg/log"

	"github.com/gorilla/websocket"
)

// ChatService defines chat operations.
type ChatService interface {
	StreamResponse(ctx context.Context, query string, user *model.User, ws *websocket.Conn, shouldStop func() bool) error
}

type chatService struct {
	searchService    SearchService
	agentService     AgentService
	llmClient        llm.Client
	conversationRepo repository.ConversationRepository
}

// NewChatService creates a new chat service.
func NewChatService(searchService SearchService, agentService AgentService, llmClient llm.Client, conversationRepo repository.ConversationRepository) ChatService {
	return &chatService{
		searchService:    searchService,
		agentService:     agentService,
		llmClient:        llmClient,
		conversationRepo: conversationRepo,
	}
}

// StreamResponse orchestrates retrieval and streams LLM output.
func (s *chatService) StreamResponse(ctx context.Context, query string, user *model.User, ws *websocket.Conn, shouldStop func() bool) error {
	history, historyErr := s.loadHistory(ctx, user.ID)
	if historyErr != nil {
		log.Errorf("Failed to load conversation history: %v", historyErr)
		history = []model.ChatMessage{}
	}

	plan := model.DefaultAgentPlan(query)
	if len(history) > 0 {
		plan.NeedHistory = true
	}
	if s.agentService != nil && s.agentService.Enabled() {
		planned, planErr := s.agentService.PlanQuery(ctx, query, history)
		if planErr != nil {
			log.Warnf("Agent planner degraded for query=%q: %v", query, planErr)
		} else {
			plan = planned
		}
		logAgentPlan(query, plan)
	}

	retrievalResults, retrievalErr := s.retrieveWithPlan(ctx, query, plan, user)
	if retrievalErr != nil {
		log.Warnf("Failed to retrieve context, fallback to chat without retrieval: %v", retrievalErr)
		retrievalResults = []model.SearchResponseDTO{}
	}

	contextText := s.buildContextText(retrievalResults)
	systemMsg := s.buildSystemMessage(contextText)
	answerHistory := s.limitConversationHistory(history, plan.NeedHistory)
	messages := s.composeMessages(systemMsg, answerHistory, query)

	answerBuilder := &strings.Builder{}
	interceptor := &wsWriterInterceptor{conn: ws, writer: answerBuilder, shouldStop: shouldStop}

	gen := s.buildGenerationParams()
	llmMsgs := make([]llm.Message, 0, len(messages))
	for _, m := range messages {
		llmMsgs = append(llmMsgs, llm.Message{Role: m.Role, Content: m.Content})
	}

	if err := s.llmClient.StreamChatMessages(ctx, llmMsgs, gen, interceptor); err != nil {
		return err
	}

	sendCompletion(ws)
	fullAnswer := answerBuilder.String()
	if len(fullAnswer) > 0 {
		if err := s.addMessageToConversation(context.Background(), user.ID, query, fullAnswer); err != nil {
			log.Errorf("Failed to save conversation history: %v", err)
		}
	}
	return nil
}

func (s *chatService) retrieveWithPlan(ctx context.Context, query string, plan model.AgentPlan, user *model.User) ([]model.SearchResponseDTO, error) {
	if plan.SkipRetrieval {
		return []model.SearchResponseDTO{}, nil
	}

	topK := config.Conf.Retrieval.FinalTopK
	if topK <= 0 {
		topK = 5
	}

	queries := []string{strings.TrimSpace(plan.RewrittenQuery)}
	if queries[0] == "" {
		queries[0] = strings.TrimSpace(query)
	}
	queries = append(queries, plan.SubQueries...)

	disableRerank := false
	if plan.EnableRerank != nil {
		disableRerank = !*plan.EnableRerank
	}

	collected := make([][]model.SearchResponseDTO, 0, len(queries))
	for idx, item := range queries {
		if strings.TrimSpace(item) == "" {
			continue
		}
		opts := SearchOptions{
			Query:         item,
			TopK:          topK,
			Mode:          plan.RetrievalMode,
			DisableRerank: disableRerank || idx > 0,
		}
		results, err := s.searchService.Search(ctx, opts, user)
		if err != nil {
			if idx == 0 {
				return nil, err
			}
			log.Warnf("sub-query retrieval degraded, query=%q err=%v", item, err)
			continue
		}
		collected = append(collected, results)
	}

	return mergeSearchResults(collected, topK), nil
}

func (s *chatService) buildContextText(searchResults []model.SearchResponseDTO) string {
	if len(searchResults) == 0 {
		return ""
	}
	const maxSnippetLen = 800
	var contextBuilder strings.Builder
	for i, r := range searchResults {
		snippet := r.TextContent
		if len(snippet) > maxSnippetLen {
			snippet = snippet[:maxSnippetLen] + "..."
		}
		fileLabel := r.FileName
		if fileLabel == "" {
			fileLabel = "unknown"
		}
		contextBuilder.WriteString(fmt.Sprintf("[%d] (%s) %s\n", i+1, fileLabel, snippet))
	}
	return contextBuilder.String()
}

func (s *chatService) buildSystemMessage(contextText string) string {
	rules := config.Conf.AI.Prompt.Rules
	if rules == "" {
		rules = config.Conf.LLM.Prompt.Rules
	}
	refStart := config.Conf.AI.Prompt.RefStart
	if refStart == "" {
		refStart = config.Conf.LLM.Prompt.RefStart
	}
	if refStart == "" {
		refStart = "<<REF>>"
	}
	refEnd := config.Conf.AI.Prompt.RefEnd
	if refEnd == "" {
		refEnd = config.Conf.LLM.Prompt.RefEnd
	}
	if refEnd == "" {
		refEnd = "<<END>>"
	}

	var sys strings.Builder
	if rules != "" {
		sys.WriteString(rules)
		sys.WriteString("\n\n")
	}
	sys.WriteString(refStart)
	sys.WriteString("\n")
	if contextText != "" {
		sys.WriteString(contextText)
	} else {
		noRes := config.Conf.AI.Prompt.NoResultText
		if noRes == "" {
			noRes = config.Conf.LLM.Prompt.NoResultText
		}
		if noRes == "" {
			noRes = "(no retrieval result in this turn)"
		}
		sys.WriteString(noRes)
		sys.WriteString("\n")
	}
	sys.WriteString(refEnd)
	return sys.String()
}

func (s *chatService) loadHistory(ctx context.Context, userID uint) ([]model.ChatMessage, error) {
	convID, err := s.conversationRepo.GetOrCreateConversationID(ctx, userID)
	if err != nil {
		return nil, err
	}
	return s.conversationRepo.GetConversationHistory(ctx, convID)
}

func (s *chatService) composeMessages(systemMsg string, history []model.ChatMessage, userInput string) []model.ChatMessage {
	msgs := make([]model.ChatMessage, 0, len(history)+2)
	msgs = append(msgs, model.ChatMessage{Role: "system", Content: systemMsg})
	msgs = append(msgs, history...)
	msgs = append(msgs, model.ChatMessage{Role: "user", Content: userInput})
	return msgs
}

func (s *chatService) addMessageToConversation(ctx context.Context, userID uint, question, answer string) error {
	conversationID, err := s.conversationRepo.GetOrCreateConversationID(ctx, userID)
	if err != nil {
		return fmt.Errorf("failed to get or create conversation ID: %w", err)
	}

	history, err := s.conversationRepo.GetConversationHistory(ctx, conversationID)
	if err != nil {
		return fmt.Errorf("failed to get conversation history: %w", err)
	}

	history = append(history, model.ChatMessage{Role: "user", Content: question, Timestamp: time.Now()})
	history = append(history, model.ChatMessage{Role: "assistant", Content: answer, Timestamp: time.Now()})

	return s.conversationRepo.UpdateConversationHistory(ctx, conversationID, history)
}

func (s *chatService) limitConversationHistory(history []model.ChatMessage, needHistory bool) []model.ChatMessage {
	if !needHistory || len(history) == 0 {
		return []model.ChatMessage{}
	}
	const maxHistoryMessages = 8
	if len(history) <= maxHistoryMessages {
		return history
	}
	return history[len(history)-maxHistoryMessages:]
}

func mergeSearchResults(groups [][]model.SearchResponseDTO, topK int) []model.SearchResponseDTO {
	if len(groups) == 0 {
		return []model.SearchResponseDTO{}
	}

	merged := make(map[string]model.SearchResponseDTO)
	for _, group := range groups {
		for _, item := range group {
			key := fmt.Sprintf("%s:%d", item.FileMD5, item.ChunkID)
			existing, ok := merged[key]
			if !ok || item.Score > existing.Score {
				merged[key] = item
			}
		}
	}

	results := make([]model.SearchResponseDTO, 0, len(merged))
	for _, item := range merged {
		results = append(results, item)
	}
	sort.SliceStable(results, func(i, j int) bool {
		if results[i].Score == results[j].Score {
			return results[i].FileMD5+fmt.Sprint(results[i].ChunkID) < results[j].FileMD5+fmt.Sprint(results[j].ChunkID)
		}
		return results[i].Score > results[j].Score
	})

	if topK > 0 && len(results) > topK {
		results = results[:topK]
	}
	return results
}

// wsWriterInterceptor wraps websocket output and captures the full answer.
type wsWriterInterceptor struct {
	conn       *websocket.Conn
	writer     *strings.Builder
	shouldStop func() bool
}

// WriteMessage implements llm.MessageWriter.
func (w *wsWriterInterceptor) WriteMessage(messageType int, data []byte) error {
	if w.shouldStop != nil && w.shouldStop() {
		return nil
	}
	if len(data) == 0 {
		return nil
	}

	w.writer.Write(data)
	payload := map[string]string{"chunk": string(data)}
	b, _ := json.Marshal(payload)
	return w.conn.WriteMessage(messageType, b)
}

func sendCompletion(ws *websocket.Conn) {
	notif := map[string]any{
		"type":      "completion",
		"status":    "finished",
		"message":   "response finished",
		"timestamp": time.Now().UnixMilli(),
		"date":      time.Now().Format("2006-01-02T15:04:05"),
	}
	b, _ := json.Marshal(notif)
	_ = ws.WriteMessage(websocket.TextMessage, b)
}

func (s *chatService) buildGenerationParams() *llm.GenerationParams {
	var gp llm.GenerationParams
	if config.Conf.LLM.Generation.Temperature != 0 {
		t := config.Conf.LLM.Generation.Temperature
		gp.Temperature = &t
	}
	if config.Conf.LLM.Generation.TopP != 0 {
		p := config.Conf.LLM.Generation.TopP
		gp.TopP = &p
	}
	if config.Conf.LLM.Generation.MaxTokens != 0 {
		m := config.Conf.LLM.Generation.MaxTokens
		gp.MaxTokens = &m
	}
	if gp.Temperature == nil && gp.TopP == nil && gp.MaxTokens == nil {
		return nil
	}
	return &gp
}
