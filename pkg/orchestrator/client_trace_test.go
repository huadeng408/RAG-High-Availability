package orchestrator

import (
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"

	"github.com/gorilla/websocket"
	"github.com/huadeng408/RAG-High-Availability/internal/config"
	"github.com/huadeng408/RAG-High-Availability/internal/model"
	"github.com/huadeng408/RAG-High-Availability/pkg/log"
)

func TestStreamResponsePropagatesTraceIDOnEveryWebSocketEvent(t *testing.T) {
	log.Init("error", "json", "")
	upstream := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		w.Header().Set("Content-Type", "application/x-ndjson")
		_, _ = fmt.Fprintln(w, `{"type":"chunk","chunk":"hello","traceId":"trace-stream"}`)
		_, _ = fmt.Fprintln(w, `{"type":"trace","trace":"generate_answer","traceId":"trace-stream"}`)
		_, _ = fmt.Fprintln(w, `{"type":"done","done":true,"traceId":"trace-stream"}`)
	}))
	defer upstream.Close()

	client := NewClient(config.AIOrchestratorConfig{Enabled: true, BaseURL: upstream.URL})
	wsServer := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		conn, err := (&websocket.Upgrader{}).Upgrade(w, r, nil)
		if err != nil {
			t.Errorf("upgrade: %v", err)
			return
		}
		defer conn.Close()
		ctx := WithTraceID(context.Background(), "trace-stream")
		_, err = client.StreamResponse(ctx, "hello", &model.User{ID: 1}, conn, nil)
		if err != nil {
			t.Errorf("stream: %v", err)
		}
	}))
	defer wsServer.Close()
	conn, _, err := websocket.DefaultDialer.Dial("ws"+strings.TrimPrefix(wsServer.URL, "http"), nil)
	if err != nil {
		t.Fatal(err)
	}
	defer conn.Close()
	_ = conn.SetReadDeadline(time.Now().Add(time.Second))
	expectedTypes := []string{"chunk", "trace"}
	for i, expectedType := range expectedTypes {
		_, payload, err := conn.ReadMessage()
		if err != nil {
			t.Fatalf("read %d: %v", i, err)
		}
		var event map[string]any
		if err := json.Unmarshal(payload, &event); err != nil {
			t.Fatal(err)
		}
		if event["traceId"] != "trace-stream" {
			t.Fatalf("event missing traceId: %s", payload)
		}
		if event["type"] != expectedType {
			t.Fatalf("event type = %v, want %s: %s", event["type"], expectedType, payload)
		}
	}
}

func TestStreamResponseRejectsConflictingUpstreamTraceID(t *testing.T) {
	log.Init("error", "json", "")
	upstream := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		w.Header().Set("Content-Type", "application/x-ndjson")
		_, _ = fmt.Fprintln(w, `{"type":"chunk","chunk":"hello","traceId":"conflicting-trace"}`)
	}))
	defer upstream.Close()
	client := NewClient(config.AIOrchestratorConfig{Enabled: true, BaseURL: upstream.URL})
	result := make(chan error, 1)
	wsServer := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		conn, _ := (&websocket.Upgrader{}).Upgrade(w, r, nil)
		defer conn.Close()
		_, err := client.StreamResponse(context.Background(), "hello", &model.User{ID: 1}, conn, nil)
		result <- err
	}))
	defer wsServer.Close()
	conn, _, err := websocket.DefaultDialer.Dial("ws"+strings.TrimPrefix(wsServer.URL, "http"), nil)
	if err != nil {
		t.Fatal(err)
	}
	defer conn.Close()
	select {
	case streamErr := <-result:
		if streamErr == nil {
			t.Fatal("expected conflicting trace ID error")
		}
	case <-time.After(time.Second):
		t.Fatal("timed out waiting for conflicting trace result")
	}
}
