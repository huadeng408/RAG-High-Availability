package embedding

import (
	"context"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	"github.com/huadeng408/RAG-High-Availability/internal/config"
	rhalog "github.com/huadeng408/RAG-High-Availability/pkg/log"
)

func TestEmbeddingAPIErrorDoesNotExposeProviderBody(t *testing.T) {
	rhalog.Init("error", "json", "")
	server := httptest.NewServer(http.HandlerFunc(func(writer http.ResponseWriter, _ *http.Request) {
		http.Error(writer, "provider credential secret-value", http.StatusBadRequest)
	}))
	defer server.Close()

	client := NewClient(config.EmbeddingConfig{BaseURL: server.URL, Model: "test-model"})
	_, err := client.CreateEmbedding(context.Background(), "hello")
	if err == nil {
		t.Fatal("CreateEmbedding error = nil, want provider failure")
	}
	if strings.Contains(err.Error(), "secret-value") {
		t.Fatalf("embedding error leaked provider body: %v", err)
	}
}

func TestDimensionsUnsupportedStillUsesSanitizedAPIErrorBody(t *testing.T) {
	err := &apiError{
		statusCode: http.StatusBadRequest,
		statusText: "400 Bad Request",
		body:       `{"error":"dimensions unsupported"}`,
	}
	if !isDimensionsUnsupported(err) {
		t.Fatal("dimensions compatibility retry was not detected")
	}
	if strings.Contains(err.Error(), "dimensions unsupported") {
		t.Fatalf("public error exposed provider body: %v", err)
	}
}
