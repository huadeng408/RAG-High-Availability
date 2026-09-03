package es

import (
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	"github.com/elastic/go-elasticsearch/v8"
	"github.com/huadeng408/RAG-High-Availability/internal/model"
	rhalog "github.com/huadeng408/RAG-High-Availability/pkg/log"
)

func TestEnsureMemoryIndexRejectsExistingNonDenseVectorMapping(t *testing.T) {
	rhalog.Init("error", "json", "")
	server := httptest.NewServer(http.HandlerFunc(func(writer http.ResponseWriter, request *http.Request) {
		writer.Header().Set("X-Elastic-Product", "Elasticsearch")
		writer.Header().Set("Content-Type", "application/json")
		switch {
		case request.Method == http.MethodHead && request.URL.Path == "/conversation_memory":
			writer.WriteHeader(http.StatusOK)
		case request.Method == http.MethodGet && strings.HasSuffix(request.URL.Path, "/_mapping"):
			_, _ = writer.Write([]byte(`{"conversation_memory":{"mappings":{"properties":{"vector":{"type":"float"}}}}}`))
		default:
			http.Error(writer, "unexpected request", http.StatusBadRequest)
		}
	}))
	defer server.Close()

	client, err := elasticsearch.NewClient(elasticsearch.Config{Addresses: []string{server.URL}})
	if err != nil {
		t.Fatal(err)
	}
	previous := ESClient
	ESClient = client
	t.Cleanup(func() { ESClient = previous })

	err = EnsureMemoryIndex("conversation_memory", 8)
	if err == nil || !strings.Contains(err.Error(), "dense_vector") {
		t.Fatalf("EnsureMemoryIndex error = %v, want dense_vector mapping rejection", err)
	}
}

func TestEnsureMemoryIndexRejectsExistingDenseVectorDimensionMismatch(t *testing.T) {
	rhalog.Init("error", "json", "")
	server := httptest.NewServer(http.HandlerFunc(func(writer http.ResponseWriter, request *http.Request) {
		writer.Header().Set("X-Elastic-Product", "Elasticsearch")
		writer.Header().Set("Content-Type", "application/json")
		switch request.Method {
		case http.MethodHead:
			writer.WriteHeader(http.StatusOK)
		case http.MethodGet:
			_, _ = writer.Write([]byte(`{"conversation_memory":{"mappings":{"properties":{"vector":{"type":"dense_vector","dims":7}}}}}`))
		default:
			http.Error(writer, "unexpected request", http.StatusBadRequest)
		}
	}))
	defer server.Close()

	client, err := elasticsearch.NewClient(elasticsearch.Config{Addresses: []string{server.URL}})
	if err != nil {
		t.Fatal(err)
	}
	previous := ESClient
	ESClient = client
	t.Cleanup(func() { ESClient = previous })

	err = EnsureMemoryIndex("conversation_memory", 8)
	if err == nil || !strings.Contains(err.Error(), "dims=7") {
		t.Fatalf("EnsureMemoryIndex error = %v, want dimension mismatch", err)
	}
}

func TestIndexMemoryDocumentDoesNotExposeElasticsearchBody(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(writer http.ResponseWriter, _ *http.Request) {
		writer.Header().Set("X-Elastic-Product", "Elasticsearch")
		http.Error(writer, "provider credential secret-value", http.StatusInternalServerError)
	}))
	defer server.Close()

	client, err := elasticsearch.NewClient(elasticsearch.Config{Addresses: []string{server.URL}})
	if err != nil {
		t.Fatal(err)
	}
	err = IndexMemoryDocument(t.Context(), client, "conversation_memory", model.MemoryEsDocument{MemoryID: "memory-1"})
	if err == nil {
		t.Fatal("IndexMemoryDocument error = nil, want Elasticsearch failure")
	}
	if strings.Contains(err.Error(), "secret-value") {
		t.Fatalf("Elasticsearch error leaked provider body: %v", err)
	}
}
