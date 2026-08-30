package service

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
	orchestratorclient "github.com/huadeng408/RAG-High-Availability/pkg/orchestrator"
)

func TestChatStreamForwardsDoneTraceAndCitationsToWebSocket(t *testing.T) {
	log.Init("error", "json", "")
	upstream := httptest.NewServer(http.HandlerFunc(func(writer http.ResponseWriter, _ *http.Request) {
		writer.Header().Set("Content-Type", "application/x-ndjson")
		_, _ = fmt.Fprintln(writer, `{"type":"chunk","chunk":"The amount is 1200."}`)
		_, _ = fmt.Fprintln(writer, `{"type":"done","done":true,"traceId":"trace-cited-answer","citations":[{"evidenceId":"e-invoice-2","documentVersion":"v-invoice","modality":"pdf","page":2,"bbox":{"x0":12,"y0":16,"x1":220,"y1":48},"excerpt":"amount 1200"}]}`)
	}))
	defer upstream.Close()

	orchestrator := orchestratorclient.NewClient(config.AIOrchestratorConfig{Enabled: true, BaseURL: upstream.URL})
	chat := NewChatService(nil, nil, nil, orchestrator)
	upgrader := websocket.Upgrader{}
	wsServer := httptest.NewServer(http.HandlerFunc(func(writer http.ResponseWriter, request *http.Request) {
		conn, err := upgrader.Upgrade(writer, request, nil)
		if err != nil {
			t.Errorf("upgrade websocket: %v", err)
			return
		}
		defer conn.Close()
		if err := chat.StreamResponse(context.Background(), "where is the amount?", &model.User{ID: 5}, conn, nil); err != nil {
			t.Errorf("chat stream: %v", err)
		}
	}))
	defer wsServer.Close()

	wsURL := "ws" + strings.TrimPrefix(wsServer.URL, "http")
	conn, _, err := websocket.DefaultDialer.Dial(wsURL, nil)
	if err != nil {
		t.Fatal(err)
	}
	defer conn.Close()

	_, chunk, err := conn.ReadMessage()
	if err != nil {
		t.Fatal(err)
	}
	if string(chunk) != `{"chunk":"The amount is 1200."}` {
		t.Fatalf("chunk = %s", chunk)
	}

	_, completionMessage, err := conn.ReadMessage()
	if err != nil {
		t.Fatal(err)
	}
	var completion struct {
		Type      string           `json:"type"`
		TraceID   string           `json:"traceId"`
		Citations []model.Citation `json:"citations"`
	}
	if err := json.Unmarshal(completionMessage, &completion); err != nil {
		t.Fatal(err)
	}
	if completion.Type != "completion" || completion.TraceID != "trace-cited-answer" {
		t.Fatalf("completion = %#v", completion)
	}
	if len(completion.Citations) != 1 || completion.Citations[0].EvidenceID != "e-invoice-2" || completion.Citations[0].Page != 2 {
		t.Fatalf("completion citations = %#v", completion.Citations)
	}
	if err := conn.SetReadDeadline(time.Now().Add(100 * time.Millisecond)); err != nil {
		t.Fatal(err)
	}
	if _, extra, err := conn.ReadMessage(); err == nil {
		t.Fatalf("unexpected extra websocket message: %s", extra)
	}
}

func TestContextConversionPreservesSearchCitations(t *testing.T) {
	results := []model.SearchResponseDTO{{
		FileMD5:     "file-md5",
		FileName:    "invoice.pdf",
		ChunkID:     2,
		TextContent: "The invoice amount is 1200.",
		Citations: []model.Citation{{
			EvidenceID:      "e-invoice-page-2",
			DocumentVersion: "v-invoice",
			Modality:        "pdf",
			Page:            2,
			BBox:            &model.BoundingBox{X0: 12, Y0: 16, X1: 220, Y1: 48},
			SourcePath:      "uploads/invoice.pdf",
		}},
	}}

	contextItems := (&chatService{}).convertSearchResultsToContext(results)
	orchestratorItems := convertContextSnippets(contextItems)
	if len(orchestratorItems) != 1 || len(orchestratorItems[0].Citations) != 1 {
		t.Fatalf("lost citations during context conversion: %#v", orchestratorItems)
	}
	citation := orchestratorItems[0].Citations[0]
	if citation.EvidenceID != "e-invoice-page-2" || citation.Page != 2 || citation.BBox == nil || citation.BBox.X1 != 220 {
		t.Fatalf("lost citation provenance: %#v", citation)
	}
}
