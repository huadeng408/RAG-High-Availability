package service

import (
	"context"
	"fmt"
	"strings"

	"pai-smart-go/internal/config"
	"pai-smart-go/internal/model"
	agentclient "pai-smart-go/pkg/agent"
	"pai-smart-go/pkg/log"
)

type AgentService interface {
	Enabled() bool
	PlanQuery(ctx context.Context, query string, history []model.ChatMessage) (model.AgentPlan, error)
}

type agentService struct {
	client agentclient.Client
	cfg    config.AgentConfig
}

func NewAgentService(client agentclient.Client, cfg config.AgentConfig) AgentService {
	return &agentService{
		client: client,
		cfg:    normalizeAgentConfig(cfg),
	}
}

func (s *agentService) Enabled() bool {
	return s.client != nil && s.client.Enabled() && s.cfg.Enabled
}

func (s *agentService) PlanQuery(ctx context.Context, query string, history []model.ChatMessage) (model.AgentPlan, error) {
	defaultPlan := model.DefaultAgentPlan(query)
	if !s.Enabled() {
		return defaultPlan, nil
	}

	intent, rawResponse, err := s.classifyIntent(ctx, query, history)
	if err != nil {
		return defaultPlan, err
	}

	plan := s.buildPlan(intent, query, history)
	if strings.TrimSpace(rawResponse) != "" {
		plan.Reason = strings.TrimSpace(rawResponse)
	}
	return plan, nil
}

func (s *agentService) classifyIntent(ctx context.Context, query string, history []model.ChatMessage) (string, string, error) {
	messages := []agentclient.Message{
		{Role: "system", Content: s.buildIntentSystemPrompt()},
		{Role: "user", Content: s.buildIntentUserPrompt(query, history)},
	}
	params := &agentclient.CompletionParams{
		Temperature: &s.cfg.Temperature,
		MaxTokens:   &s.cfg.MaxTokens,
	}

	raw, err := s.client.Complete(ctx, messages, params)
	if err != nil {
		return "", "", err
	}

	intent := parseIntentLabel(raw)
	if intent == "" {
		intent = heuristicIntent(query, history)
	}

	return intent, raw, nil
}

func (s *agentService) buildIntentSystemPrompt() string {
	return "You are an intent classifier for a knowledge-base assistant. Reply with exactly one label from: single_hop, follow_up, comparison, troubleshooting, chitchat. Do not output anything else."
}

func (s *agentService) buildIntentUserPrompt(query string, history []model.ChatMessage) string {
	var builder strings.Builder
	builder.WriteString("Recent conversation history:\n")
	if len(history) == 0 {
		builder.WriteString("(empty)\n")
	} else {
		for _, item := range limitAgentHistory(history, s.cfg.HistoryTurns) {
			builder.WriteString(fmt.Sprintf("- %s: %s\n", item.Role, strings.TrimSpace(item.Content)))
		}
	}
	builder.WriteString("\nCurrent user question:\n")
	builder.WriteString(strings.TrimSpace(query))
	return builder.String()
}

func normalizeAgentConfig(cfg config.AgentConfig) config.AgentConfig {
	if cfg.TimeoutMs <= 0 {
		cfg.TimeoutMs = 1500
	}
	if cfg.MaxSubQueries <= 0 {
		cfg.MaxSubQueries = 2
	}
	if cfg.HistoryTurns <= 0 {
		cfg.HistoryTurns = 6
	}
	if cfg.Temperature == 0 {
		cfg.Temperature = 0.1
	}
	if cfg.MaxTokens <= 0 {
		cfg.MaxTokens = 256
	}
	if cfg.ContextWindow <= 0 {
		cfg.ContextWindow = 1024
	}
	return cfg
}

func (s *agentService) buildPlan(intent string, query string, history []model.ChatMessage) model.AgentPlan {
	plan := model.DefaultAgentPlan(query)
	if strings.TrimSpace(intent) == "" {
		intent = heuristicIntent(query, history)
	}

	plan.Intent = intent
	plan.RewrittenQuery = rewriteQuery(query)
	plan.NeedHistory = intent == "follow_up" || shouldUseHistory(query, history)
	plan.SkipRetrieval = intent == "chitchat"

	switch intent {
	case "troubleshooting":
		plan.RetrievalMode = model.RetrievalModeBM25
	case "comparison":
		plan.RetrievalMode = model.RetrievalModeHybrid
		enable := true
		plan.EnableRerank = &enable
	default:
		plan.RetrievalMode = model.RetrievalModeHybrid
	}

	return plan
}

func limitAgentHistory(history []model.ChatMessage, maxTurns int) []model.ChatMessage {
	if maxTurns <= 0 || len(history) <= maxTurns {
		return history
	}
	return history[len(history)-maxTurns:]
}

func parseIntentLabel(raw string) string {
	candidate := strings.ToLower(strings.TrimSpace(raw))
	labels := []string{"single_hop", "follow_up", "comparison", "troubleshooting", "chitchat"}
	for _, label := range labels {
		if strings.Contains(candidate, label) {
			return label
		}
	}
	return ""
}

func heuristicIntent(query string, history []model.ChatMessage) string {
	q := strings.ToLower(strings.TrimSpace(query))
	switch {
	case strings.Contains(q, "你好") || strings.Contains(q, "你是谁") || strings.Contains(q, "hi") || strings.Contains(q, "hello"):
		return "chitchat"
	case strings.Contains(q, "继续追问") || strings.Contains(q, "刚才") || strings.Contains(q, "上一轮") || strings.Contains(q, "这四个阶段") || strings.Contains(q, "这个系统"):
		if len(history) > 0 {
			return "follow_up"
		}
	case strings.Contains(q, "区别") || strings.Contains(q, "对比") || strings.Contains(q, "相比") || strings.Contains(q, "优先级"):
		return "comparison"
	case strings.Contains(q, "失败") || strings.Contains(q, "报错") || strings.Contains(q, "排查") || strings.Contains(q, "异常") || strings.Contains(q, "为什么"):
		return "troubleshooting"
	}
	return "single_hop"
}

func rewriteQuery(query string) string {
	replacer := strings.NewReplacer(
		"继续追问：", "",
		"继续追问:", "",
		"请直接回答", "",
		"请直接", "",
		"请回答", "",
	)
	out := strings.TrimSpace(replacer.Replace(query))
	if out == "" {
		return strings.TrimSpace(query)
	}
	return out
}

func shouldUseHistory(query string, history []model.ChatMessage) bool {
	if len(history) == 0 {
		return false
	}
	q := strings.ToLower(strings.TrimSpace(query))
	return strings.Contains(q, "继续追问") ||
		strings.Contains(q, "这个") ||
		strings.Contains(q, "这四个") ||
		strings.Contains(q, "上面") ||
		strings.Contains(q, "刚才")
}

func logAgentPlan(query string, plan model.AgentPlan) {
	log.Infow("[AgentService] planned query",
		"query", query,
		"intent", plan.Intent,
		"rewrittenQuery", plan.RewrittenQuery,
		"subQueries", plan.SubQueries,
		"retrievalMode", plan.RetrievalMode,
		"enableRerank", plan.EnableRerank,
		"needHistory", plan.NeedHistory,
		"skipRetrieval", plan.SkipRetrieval,
		"reason", plan.Reason,
	)
}
