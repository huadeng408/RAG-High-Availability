package orchestrator

import (
	"context"
	"fmt"
	"net/http"
	"net/http/httptest"
	"testing"

	"github.com/huadeng408/RAG-High-Availability/internal/config"
	"github.com/huadeng408/RAG-High-Availability/pkg/log"
	"github.com/huadeng408/RAG-High-Availability/pkg/tasks"
)

func TestParseReturnsStructuredEvidenceDocument(t *testing.T) {
	log.Init("error", "json", "")
	server := httptest.NewServer(http.HandlerFunc(func(writer http.ResponseWriter, request *http.Request) {
		if request.URL.Path != "/v1/ingestion/parse" {
			t.Fatalf("unexpected path: %s", request.URL.Path)
		}
		writer.Header().Set("Content-Type", "application/json")
		_, _ = fmt.Fprint(writer, `{"code":200,"data":{"parsedDocument":{"documentVersion":"v-pdf","modality":"pdf","parserReceipt":{"engine":"mineru+ocr","ocrPerformed":true},"evidenceUnits":[{"evidenceId":"pdf-page-2","documentVersion":"v-pdf","modality":"pdf","elementType":"ocr_text","page":2,"bbox":{"x0":1,"y0":2,"x1":3,"y1":4},"text":"Payment terms"}],"chunks":[{"id":"v-pdf:pdf-page-2:chunk","documentVersion":"v-pdf","text":"Payment terms","modality":"pdf","page":2,"evidenceIds":["pdf-page-2"]}]}}}`)
	}))
	defer server.Close()

	client := &httpIngestionClient{cfg: config.AIOrchestratorConfig{BaseURL: server.URL}, client: server.Client()}
	parsed, err := client.Parse(context.Background(), tasks.FileProcessingTask{FileMD5: "checksum", DocumentVersion: "v-pdf", FileName: "receipt.pdf"}, server.URL+"/source")
	if err != nil {
		t.Fatal(err)
	}
	if parsed.DocumentVersion != "v-pdf" || len(parsed.EvidenceUnits) != 1 || parsed.EvidenceUnits[0].Page != 2 {
		t.Fatalf("lost page evidence: %#v", parsed)
	}
	if len(parsed.Chunks) != 1 || parsed.Chunks[0].EvidenceIDs[0] != "pdf-page-2" {
		t.Fatalf("lost structured chunk linkage: %#v", parsed.Chunks)
	}
}
