package service

import (
	"context"
	"fmt"
	"strings"
	"time"

	"github.com/elastic/go-elasticsearch/v8"
	"github.com/huadeng408/RAG-High-Availability/internal/config"
	"github.com/huadeng408/RAG-High-Availability/internal/model"
	"github.com/huadeng408/RAG-High-Availability/internal/repository"
	"github.com/huadeng408/RAG-High-Availability/pkg/embedding"
	"github.com/huadeng408/RAG-High-Availability/pkg/es"
	"github.com/huadeng408/RAG-High-Availability/pkg/log"
)

func memoryIndexRetryDelay(attempt int) time.Duration {
	if attempt < 1 {
		attempt = 1
	}
	if attempt > 9 {
		attempt = 9
	}
	return time.Second * time.Duration(1<<(attempt-1))
}

func dispatchMemoryIndexOnce(
	ctx context.Context,
	repo repository.MemoryRepository,
	embeddingClient embedding.Client,
	esClient *elasticsearch.Client,
	cfg config.MemoryConfig,
	batchSize int,
	lease time.Duration,
) (int, error) {
	claimed, err := repo.ClaimPendingLongTermMemories(ctx, batchSize, lease)
	if err != nil {
		return 0, err
	}
	indexed := 0
	failures := 0
	for _, entry := range claimed {
		text := strings.TrimSpace(entry.Summary)
		if text == "" {
			text = strings.TrimSpace(entry.Content)
		}
		vector, indexErr := embeddingClient.CreateEmbedding(ctx, text)
		if indexErr == nil {
			indexErr = es.IndexMemoryDocument(ctx, esClient, cfg.MemoryIndexName, model.MemoryEsDocument{
				MemoryID:       entry.MemoryID,
				UserID:         entry.UserID,
				ConversationID: entry.ConversationID,
				MemoryType:     entry.MemoryType,
				TextContent:    text,
				Vector:         vector,
				Importance:     entry.Importance,
				CreatedAt:      entry.CreatedAt,
			})
		}
		if indexErr != nil {
			nextAttempt := time.Now().Add(memoryIndexRetryDelay(entry.IndexAttemptCount))
			errorType := fmt.Sprintf("%T", indexErr)
			if markErr := repo.MarkLongTermMemoryIndexFailed(entry.MemoryID, entry.IndexAttemptCount, errorType, nextAttempt); markErr != nil {
				failures++
				continue
			}
			failures++
			continue
		}
		if err := repo.MarkLongTermMemoryIndexed(entry.MemoryID, entry.IndexAttemptCount); err != nil {
			failures++
			continue
		}
		indexed++
	}
	if failures > 0 {
		return indexed, fmt.Errorf("long-term memory index dispatch completed with %d failure(s)", failures)
	}
	return indexed, nil
}

// RunMemoryIndexDispatcher continuously drains durable long-term-memory indexing work.
func RunMemoryIndexDispatcher(
	ctx context.Context,
	repo repository.MemoryRepository,
	embeddingClient embedding.Client,
	esClient *elasticsearch.Client,
	cfg config.MemoryConfig,
	interval time.Duration,
) {
	if !cfg.Enabled || repo == nil || embeddingClient == nil || esClient == nil {
		return
	}
	cfg = config.NormalizeMemoryConfig(cfg)
	if interval <= 0 {
		interval = time.Second
	}
	drain := func() {
		for {
			indexed, err := dispatchMemoryIndexOnce(ctx, repo, embeddingClient, esClient, cfg, 20, time.Minute)
			if err != nil {
				if ctx.Err() == nil {
					log.Warnf("long-term memory index dispatch failed: %v", err)
				}
				return
			}
			if indexed < 20 {
				return
			}
		}
	}
	drain()
	timer := time.NewTimer(interval)
	defer timer.Stop()
	for {
		select {
		case <-ctx.Done():
			return
		case <-timer.C:
			drain()
			timer.Reset(interval)
		}
	}
}
