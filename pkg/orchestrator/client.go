package orchestrator

import (
	"bufio"
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"strings"
	"time"

	"github.com/huadeng408/RAG-High-Availability/internal/config"
	"github.com/huadeng408/RAG-High-Availability/internal/model"
	"github.com/huadeng408/RAG-High-Availability/pkg/log"

	"github.com/gorilla/websocket"
)

// Client defines the external LangGraph orchestrator client.
type Client interface {
	Enabled() bool
	StreamResponse(ctx context.Context, query string, user *model.User, ws *websocket.Conn, shouldStop func() bool) (StreamCompletion, error)
}

// StreamCompletion is the terminal metadata returned by the LangGraph stream.
type StreamCompletion struct {
	TraceID   string           `json:"traceId"`
	Citations []model.Citation `json:"citations"`
}

type noopClient struct{}

// Enabled reports whether the noop client is active.
func (noopClient) Enabled() bool {
	return false
}

// StreamResponse implements the disabled client behavior.
func (noopClient) StreamResponse(ctx context.Context, query string, user *model.User, ws *websocket.Conn, shouldStop func() bool) (StreamCompletion, error) {
	return StreamCompletion{}, fmt.Errorf("ai orchestrator is disabled")
}

type httpClient struct {
	cfg    config.AIOrchestratorConfig
	client *http.Client
}

type streamRequest struct {
	Query string                 `json:"query"`
	User  model.OrchestratorUser `json:"user"`
}

type streamEvent struct {
	Type      string           `json:"type"`
	Chunk     string           `json:"chunk,omitempty"`
	Error     string           `json:"error,omitempty"`
	Trace     string           `json:"trace,omitempty"`
	Done      bool             `json:"done,omitempty"`
	TraceID   string           `json:"traceId,omitempty"`
	Citations []model.Citation `json:"citations,omitempty"`
	Final     string           `json:"final,omitempty"`
	Extra     []string         `json:"extra,omitempty"`
}

// NewClient constructs the external LangGraph orchestrator client.
func NewClient(cfg config.AIOrchestratorConfig) Client {
	if !cfg.Enabled || strings.TrimSpace(cfg.BaseURL) == "" {
		return noopClient{}
	}
	return &httpClient{
		cfg: cfg,
		// Streaming chat can legitimately run for several minutes when retrieval,
		// rerank, or downstream model calls are slow. Avoid a fixed client-wide
		// timeout and let the request context / websocket cancellation control the
		// lifecycle instead.
		client: &http.Client{},
	}
}

// Enabled reports whether the HTTP orchestrator client is active.
func (c *httpClient) Enabled() bool {
	return true
}

// StreamResponse proxies an external orchestrator stream into the websocket contract expected by the frontend.
func (c *httpClient) StreamResponse(ctx context.Context, query string, user *model.User, ws *websocket.Conn, shouldStop func() bool) (StreamCompletion, error) {
	if user == nil {
		return StreamCompletion{}, fmt.Errorf("user is required")
	}

	reqCtx, cancel := context.WithCancel(ctx)
	defer cancel()
	reqCtx, traceID := EnsureTraceID(reqCtx)
	completion := StreamCompletion{TraceID: traceID, Citations: []model.Citation{}}
	streamTraceID := traceID
	resolveEventTrace := func(eventTrace string) (string, error) {
		eventTrace = strings.TrimSpace(eventTrace)
		if eventTrace != "" {
			if streamTraceID == "" {
				streamTraceID = eventTrace
			} else if streamTraceID != eventTrace {
				return "", fmt.Errorf("orchestrator stream trace ID conflict: expected=%s got=%s", streamTraceID, eventTrace)
			}
		}
		if streamTraceID != "" {
			return streamTraceID, nil
		}
		return traceID, nil
	}

	done := make(chan struct{})
	defer close(done)
	if shouldStop != nil {
		go func() {
			ticker := time.NewTicker(120 * time.Millisecond)
			defer ticker.Stop()
			for {
				select {
				case <-done:
					return
				case <-reqCtx.Done():
					return
				case <-ticker.C:
					if shouldStop() {
						cancel()
						return
					}
				}
			}
		}()
	}

	bodyBytes, err := json.Marshal(streamRequest{
		Query: query,
		User:  model.NewOrchestratorUser(user),
	})
	if err != nil {
		return StreamCompletion{}, fmt.Errorf("marshal orchestrator request failed: %w", err)
	}

	baseURL := strings.TrimRight(strings.TrimSpace(c.cfg.BaseURL), "/")
	req, err := http.NewRequestWithContext(reqCtx, http.MethodPost, baseURL+"/v1/chat/stream", bytes.NewReader(bodyBytes))
	if err != nil {
		return StreamCompletion{}, fmt.Errorf("create orchestrator request failed: %w", err)
	}
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("Accept", "application/x-ndjson")
	if token := strings.TrimSpace(c.cfg.SharedSecret); token != "" {
		req.Header.Set("X-Internal-Token", token)
	}
	req.Header.Set("X-Trace-ID", traceID)

	start := time.Now()
	log.Infow("[OrchestratorClient] stream start",
		"trace_id", traceID,
		"user_id", user.ID,
		"query", query,
	)

	resp, err := c.client.Do(req)
	if err != nil {
		if reqCtx.Err() == context.Canceled && shouldStop != nil && shouldStop() {
			log.Infow("[OrchestratorClient] stream canceled", "trace_id", traceID, "latency_ms", time.Since(start).Milliseconds())
			return completion, nil
		}
		return StreamCompletion{}, fmt.Errorf("call orchestrator failed: %w", err)
	}
	defer resp.Body.Close()

	if resp.StatusCode != http.StatusOK {
		raw, _ := io.ReadAll(resp.Body)
		return StreamCompletion{}, fmt.Errorf("orchestrator returned status=%s body=%s", resp.Status, strings.TrimSpace(string(raw)))
	}

	reader := bufio.NewReader(resp.Body)
	for {
		line, readErr := reader.ReadBytes('\n')
		if len(line) > 0 {
			var event streamEvent
			if err := json.Unmarshal(bytes.TrimSpace(line), &event); err == nil {
				eventTrace, traceErr := resolveEventTrace(event.TraceID)
				if traceErr != nil {
					return StreamCompletion{}, traceErr
				}
				switch event.Type {
				case "chunk":
					if event.Chunk != "" {
						if shouldStop != nil && shouldStop() {
							cancel()
							return completion, nil
						}
						payload, _ := json.Marshal(map[string]string{"type": "chunk", "chunk": event.Chunk, "traceId": eventTrace})
						if err := ws.WriteMessage(websocket.TextMessage, payload); err != nil {
							return StreamCompletion{}, fmt.Errorf("write websocket chunk failed: %w", err)
						}
					}
				case "error":
					payload, _ := json.Marshal(map[string]string{"type": "error", "error": event.Error, "traceId": eventTrace})
					if err := ws.WriteMessage(websocket.TextMessage, payload); err != nil {
						return StreamCompletion{}, fmt.Errorf("write websocket error failed: %w", err)
					}
					if event.Error != "" {
						return StreamCompletion{}, fmt.Errorf("orchestrator stream error: %s", event.Error)
					}
				case "trace":
					payload, _ := json.Marshal(map[string]string{"type": "trace", "trace": event.Trace, "traceId": eventTrace})
					if err := ws.WriteMessage(websocket.TextMessage, payload); err != nil {
						return StreamCompletion{}, fmt.Errorf("write websocket trace failed: %w", err)
					}
				case "done":
					completion.TraceID = eventTrace
					completion.Citations = deduplicateCitations(event.Citations)
				}
			}
		}

		if readErr != nil {
			if readErr == io.EOF || (reqCtx.Err() == context.Canceled && shouldStop != nil && shouldStop()) {
				log.Infow("[OrchestratorClient] stream finished",
					"trace_id", traceID,
					"latency_ms", time.Since(start).Milliseconds(),
				)
				return completion, nil
			}
			return StreamCompletion{}, fmt.Errorf("read orchestrator stream failed: %w", readErr)
		}
	}
}

func deduplicateCitations(citations []model.Citation) []model.Citation {
	unique := make([]model.Citation, 0, len(citations))
	seen := make(map[string]struct{}, len(citations))
	for _, citation := range citations {
		if citation.EvidenceID == "" {
			continue
		}
		if _, exists := seen[citation.EvidenceID]; exists {
			continue
		}
		seen[citation.EvidenceID] = struct{}{}
		unique = append(unique, citation)
	}
	return unique
}
