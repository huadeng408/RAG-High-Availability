package service

import (
	"context"
	"encoding/json"
	"errors"
	"net/http"
	"net/http/httptest"
	"path/filepath"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/elastic/go-elasticsearch/v8"
	"github.com/glebarez/sqlite"
	"github.com/huadeng408/RAG-High-Availability/internal/config"
	"github.com/huadeng408/RAG-High-Availability/internal/model"
	"github.com/huadeng408/RAG-High-Availability/internal/repository"
	"gorm.io/gorm"
)

type recoverableMemoryEmbedding struct {
	mu       sync.Mutex
	failures int
	failure  error
}

func (e *recoverableMemoryEmbedding) CreateEmbedding(context.Context, string) ([]float32, error) {
	e.mu.Lock()
	defer e.mu.Unlock()
	if e.failures > 0 {
		e.failures--
		if e.failure != nil {
			return nil, e.failure
		}
		return nil, errors.New("embedding unavailable")
	}
	return []float32{0.25, 0.75}, nil
}

func TestMemoryIndexDispatcherSanitizesFailureAndContinuesClaimedBatch(t *testing.T) {
	db, repo := openMemoryDispatcherDatabase(t)
	entries := []*model.LongTermMemory{
		{
			MemoryID: "memory-fails", UserID: 12, ConversationID: "conversation-12",
			MemoryType: "project", Content: "first marker", Summary: "first marker",
			Importance: 0.9, IndexStatus: model.MemoryIndexPending,
		},
		{
			MemoryID: "memory-succeeds", UserID: 12, ConversationID: "conversation-12",
			MemoryType: "project", Content: "second marker", Summary: "second marker",
			Importance: 0.8, IndexStatus: model.MemoryIndexPending,
		},
	}
	for _, entry := range entries {
		if err := repo.CreateLongTermMemory(entry); err != nil {
			t.Fatal(err)
		}
	}

	server := httptest.NewServer(http.HandlerFunc(func(writer http.ResponseWriter, request *http.Request) {
		defer request.Body.Close()
		writer.Header().Set("X-Elastic-Product", "Elasticsearch")
		writer.Header().Set("Content-Type", "application/json")
		_, _ = writer.Write([]byte(`{"result":"created"}`))
	}))
	defer server.Close()
	esClient, err := elasticsearch.NewClient(elasticsearch.Config{Addresses: []string{server.URL}})
	if err != nil {
		t.Fatal(err)
	}
	embedding := &recoverableMemoryEmbedding{
		failures: 1,
		failure:  errors.New("provider credential secret-value"),
	}

	processed, dispatchErr := dispatchMemoryIndexOnce(
		context.Background(), repo, embedding, esClient,
		config.MemoryConfig{MemoryIndexName: "conversation_memory"}, 10, time.Minute,
	)
	if dispatchErr == nil || processed != 1 {
		t.Fatalf("dispatch processed=%d err=%v, want one success and one sanitized failure", processed, dispatchErr)
	}
	if strings.Contains(dispatchErr.Error(), "secret-value") {
		t.Fatalf("dispatch error leaked provider body: %v", dispatchErr)
	}

	var failed model.LongTermMemory
	if err := db.Where("memory_id = ?", "memory-fails").First(&failed).Error; err != nil {
		t.Fatal(err)
	}
	if failed.IndexStatus != model.MemoryIndexPending || failed.IndexLastError == "" {
		t.Fatalf("failed row did not return to pending: %#v", failed)
	}
	if strings.Contains(failed.IndexLastError, "secret-value") {
		t.Fatalf("persisted failure leaked provider body: %q", failed.IndexLastError)
	}

	var succeeded model.LongTermMemory
	if err := db.Where("memory_id = ?", "memory-succeeds").First(&succeeded).Error; err != nil {
		t.Fatal(err)
	}
	if succeeded.IndexStatus != model.MemoryIndexIndexed || succeeded.IndexedAt == nil {
		t.Fatalf("later claimed row was not processed: %#v", succeeded)
	}
}

func (e *recoverableMemoryEmbedding) CreateEmbeddings(ctx context.Context, texts []string) ([][]float32, error) {
	vectors := make([][]float32, 0, len(texts))
	for _, text := range texts {
		vector, err := e.CreateEmbedding(ctx, text)
		if err != nil {
			return nil, err
		}
		vectors = append(vectors, vector)
	}
	return vectors, nil
}

func openMemoryDispatcherDatabase(t *testing.T) (*gorm.DB, repository.MemoryRepository) {
	t.Helper()
	db, err := gorm.Open(sqlite.Open(filepath.Join(t.TempDir(), "memory-dispatcher.db")), &gorm.Config{})
	if err != nil {
		t.Fatal(err)
	}
	if err := db.AutoMigrate(&model.LongTermMemory{}); err != nil {
		t.Fatal(err)
	}
	sqlDB, err := db.DB()
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() {
		if err := sqlDB.Close(); err != nil {
			t.Errorf("close memory dispatcher database: %v", err)
		}
	})
	return db, repository.NewMemoryRepository(db)
}

func TestMemoryIndexDispatcherRetriesDurableEntryIdempotently(t *testing.T) {
	db, repo := openMemoryDispatcherDatabase(t)
	entry := &model.LongTermMemory{
		MemoryID: "memory-recovery", UserID: 11, ConversationID: "conversation-11",
		MemoryType: "project", Content: "durable marker", Summary: "durable marker",
		Importance: 0.9, IndexStatus: model.MemoryIndexPending,
	}
	if err := repo.CreateLongTermMemory(entry); err != nil {
		t.Fatal(err)
	}

	var mu sync.Mutex
	indexed := make([]model.MemoryEsDocument, 0, 1)
	server := httptest.NewServer(http.HandlerFunc(func(writer http.ResponseWriter, request *http.Request) {
		defer request.Body.Close()
		writer.Header().Set("X-Elastic-Product", "Elasticsearch")
		var document model.MemoryEsDocument
		if err := json.NewDecoder(request.Body).Decode(&document); err != nil {
			http.Error(writer, err.Error(), http.StatusBadRequest)
			return
		}
		mu.Lock()
		indexed = append(indexed, document)
		mu.Unlock()
		writer.Header().Set("Content-Type", "application/json")
		_, _ = writer.Write([]byte(`{"result":"created"}`))
	}))
	defer server.Close()
	esClient, err := elasticsearch.NewClient(elasticsearch.Config{Addresses: []string{server.URL}})
	if err != nil {
		t.Fatal(err)
	}
	embedding := &recoverableMemoryEmbedding{failures: 1}
	cfg := config.MemoryConfig{MemoryIndexName: "conversation_memory"}

	processed, err := dispatchMemoryIndexOnce(context.Background(), repo, embedding, esClient, cfg, 10, time.Minute)
	if err == nil || processed != 0 {
		t.Fatalf("first dispatch processed=%d err=%v, want durable failure", processed, err)
	}
	var failed model.LongTermMemory
	if err := db.Where("memory_id = ?", entry.MemoryID).First(&failed).Error; err != nil {
		t.Fatal(err)
	}
	if failed.IndexStatus != model.MemoryIndexPending || failed.IndexAttemptCount != 1 || failed.IndexLastError == "" {
		t.Fatalf("failed attempt was not persisted: %#v", failed)
	}
	if err := db.Model(&model.LongTermMemory{}).Where("memory_id = ?", entry.MemoryID).
		Update("index_next_attempt_at", time.Now().Add(-time.Second)).Error; err != nil {
		t.Fatal(err)
	}

	processed, err = dispatchMemoryIndexOnce(context.Background(), repo, embedding, esClient, cfg, 10, time.Minute)
	if err != nil || processed != 1 {
		t.Fatalf("recovery dispatch processed=%d err=%v", processed, err)
	}
	var recovered model.LongTermMemory
	if err := db.Where("memory_id = ?", entry.MemoryID).First(&recovered).Error; err != nil {
		t.Fatal(err)
	}
	if recovered.IndexStatus != model.MemoryIndexIndexed || recovered.IndexAttemptCount != 2 || recovered.IndexedAt == nil {
		t.Fatalf("recovered state = %#v", recovered)
	}
	mu.Lock()
	defer mu.Unlock()
	if len(indexed) != 1 || indexed[0].MemoryID != entry.MemoryID || indexed[0].TextContent != entry.Summary {
		t.Fatalf("indexed documents = %#v", indexed)
	}
}
