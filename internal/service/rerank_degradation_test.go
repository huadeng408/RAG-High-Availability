package service

import (
	"context"
	"testing"
	"time"

	"github.com/huadeng408/RAG-High-Availability/internal/model"
	"github.com/huadeng408/RAG-High-Availability/pkg/reranker"
)

type slowReranker struct{}

func (slowReranker) Enabled() bool { return true }

func (slowReranker) Rerank(ctx context.Context, _ string, _ []reranker.Document, _ int) ([]reranker.Result, error) {
	select {
	case <-time.After(100 * time.Millisecond):
		return []reranker.Result{{Index: 0, Score: 1}}, nil
	case <-ctx.Done():
		return nil, ctx.Err()
	}
}

func TestRerankerTimeoutReturnsFusedHits(t *testing.T) {
	hits, applied, timedOut := rerankWithDeadline(context.Background(), slowReranker{}, []retrievalHit{{ID: "h1", Source: model.EsDocument{TextContent: "fallback"}}}, 1)
	if len(hits) != 1 || applied || !timedOut {
		t.Fatalf("timeout did not degrade: hits=%d applied=%t timedOut=%t", len(hits), applied, timedOut)
	}
}
