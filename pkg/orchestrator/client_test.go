package orchestrator

import (
	"context"
	"fmt"
	"net/http"
	"net/http/httptest"
	"testing"
	"time"

	"github.com/gorilla/websocket"
	"github.com/huadeng408/RAG-High-Availability/internal/config"
	"github.com/huadeng408/RAG-High-Availability/internal/model"
	"github.com/huadeng408/RAG-High-Availability/pkg/log"
)

func TestStreamResponseReturnsDoneTraceAndCitations(t *testing.T) {
	log.Init("error", "json", "")
	upstream := httptest.NewServer(http.HandlerFunc(func(writer http.ResponseWriter, request *http.Request) {
		if request.Header.Get("X-Trace-ID") == "" {
			t.Fatal("missing trace header")
		}
		writer.Header().Set("Content-Type", "application/x-ndjson")
		_, _ = fmt.Fprintln(writer, `{"type":"done","done":true,"traceId":"trace-from-python","citations":[{"evidenceId":"e-page-2","documentVersion":"v-document","modality":"pdf","page":2,"bbox":{"x0":1,"y0":2,"x1":3,"y1":4},"excerpt":"evidence excerpt","sourcePath":"uploads/document.pdf"},{"evidenceId":"e-page-2","documentVersion":"v-document","modality":"pdf","page":2}]}`)
	}))
	defer upstream.Close()

	finished := make(chan struct{})
	upgrader := websocket.Upgrader{}
	websocketServer := httptest.NewServer(http.HandlerFunc(func(writer http.ResponseWriter, request *http.Request) {
		defer close(finished)
		conn, err := upgrader.Upgrade(writer, request, nil)
		if err != nil {
			t.Errorf("upgrade websocket: %v", err)
			return
		}
		defer conn.Close()

		client := &httpClient{
			cfg:    config.AIOrchestratorConfig{Enabled: true, BaseURL: upstream.URL},
			client: upstream.Client(),
		}
		completion, err := client.StreamResponse(context.Background(), "where is the amount?", &model.User{ID: 7}, conn, nil)
		if err != nil {
			t.Errorf("stream response: %v", err)
			return
		}
		if completion.TraceID != "trace-from-python" {
			t.Errorf("completion trace ID = %q", completion.TraceID)
		}
		if len(completion.Citations) != 1 || completion.Citations[0].EvidenceID != "e-page-2" || completion.Citations[0].Page != 2 {
			t.Errorf("completion citations = %#v", completion.Citations)
		}
	}))
	defer websocketServer.Close()

	wsURL := "ws" + websocketServer.URL[len("http"):]
	conn, _, err := websocket.DefaultDialer.Dial(wsURL, nil)
	if err != nil {
		t.Fatal(err)
	}
	defer conn.Close()

	select {
	case <-finished:
	case <-time.After(2 * time.Second):
		t.Fatal("stream response did not finish")
	}
}
