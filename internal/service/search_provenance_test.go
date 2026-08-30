package service

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"testing"

	"github.com/elastic/go-elasticsearch/v8"
	"github.com/huadeng408/RAG-High-Availability/internal/model"
	"github.com/huadeng408/RAG-High-Availability/internal/repository"
)

type provenanceUploadRepository struct {
	repository.UploadRepository
	files []*model.FileUpload
}

func (r provenanceUploadRepository) FindBatchByMD5s(_ []string) ([]*model.FileUpload, error) {
	return r.files, nil
}

func TestSearchOnceRequestsEvidenceSourceFieldsAndBuildsCitation(t *testing.T) {
	requiredFields := map[string]struct{}{
		"document_version": {},
		"modality":         {},
		"page":             {},
		"slide":            {},
		"sheet":            {},
		"evidence_ids":     {},
		"bbox":             {},
	}
	server := httptest.NewServer(http.HandlerFunc(func(writer http.ResponseWriter, request *http.Request) {
		defer request.Body.Close()
		writer.Header().Set("X-Elastic-Product", "Elasticsearch")
		var body map[string]any
		if err := json.NewDecoder(request.Body).Decode(&body); err != nil {
			t.Errorf("decode search body: %v", err)
			return
		}
		requested, _ := body["_source"].([]any)
		requestedFields := make(map[string]struct{}, len(requested))
		for _, field := range requested {
			if name, ok := field.(string); ok {
				requestedFields[name] = struct{}{}
			}
		}
		allProvenanceRequested := true
		for field := range requiredFields {
			if _, ok := requestedFields[field]; !ok {
				allProvenanceRequested = false
			}
		}

		source := map[string]any{
			"file_md5":     "file-md5",
			"chunk_id":     2,
			"text_content": "The invoice amount is 1200.",
			"user_id":      7,
			"org_tag":      "finance",
			"is_public":    false,
		}
		if allProvenanceRequested {
			source["document_version"] = "v-invoice"
			source["modality"] = "pdf"
			source["page"] = 2
			source["slide"] = 0
			source["sheet"] = ""
			source["evidence_ids"] = []string{"e-invoice-page-2"}
			source["bbox"] = map[string]float64{"x0": 12, "y0": 16, "x1": 220, "y1": 48}
		}
		_ = json.NewEncoder(writer).Encode(map[string]any{
			"hits": map[string]any{"hits": []any{map[string]any{"_id": "hit-1", "_score": 1.0, "_source": source}}},
		})
	}))
	defer server.Close()

	client, err := elasticsearch.NewClient(elasticsearch.Config{Addresses: []string{server.URL}})
	if err != nil {
		t.Fatal(err)
	}
	service := &searchService{
		esClient:   client,
		indexName:  "rha-knowledge-active",
		uploadRepo: provenanceUploadRepository{files: []*model.FileUpload{{FileMD5: "file-md5", FileName: "invoice.pdf"}}},
	}

	hits, err := service.searchOnce(context.Background(), map[string]any{"query": map[string]any{"match_all": map[string]any{}}, "_source": buildSourceFields()})
	if err != nil {
		t.Fatal(err)
	}
	results, err := service.buildResponseDTOs(hits)
	if err != nil {
		t.Fatal(err)
	}
	if len(results) != 1 || len(results[0].Citations) != 1 {
		t.Fatalf("missing citation from Elasticsearch hit: %#v", results)
	}
	citation := results[0].Citations[0]
	if citation.EvidenceID != "e-invoice-page-2" || citation.Page != 2 || citation.BBox == nil || citation.BBox.X0 != 12 || citation.SourcePath != "merged/file-md5/invoice.pdf" {
		t.Fatalf("lost search provenance: %#v", citation)
	}
}
