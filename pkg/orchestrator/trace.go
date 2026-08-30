package orchestrator

import (
	"context"
	"crypto/rand"
	"encoding/hex"
	"strings"

	"github.com/huadeng408/RAG-High-Availability/pkg/observability"
)

type traceIDKey struct{}

// WithTraceID stores a trace id in context for downstream orchestrator or ingestion calls.
func WithTraceID(ctx context.Context, traceID string) context.Context {
	traceID = strings.TrimSpace(traceID)
	if traceID == "" {
		return ctx
	}
	return observability.WithTraceID(context.WithValue(ctx, traceIDKey{}, traceID), traceID)
}

// TraceIDFromContext loads the current trace id from context, if present.
func TraceIDFromContext(ctx context.Context) string {
	if ctx == nil {
		return ""
	}
	value, _ := ctx.Value(traceIDKey{}).(string)
	if value = strings.TrimSpace(value); value != "" {
		return value
	}
	return observability.TraceID(ctx)
}

// EnsureTraceID returns a context that always carries a trace id plus the resolved trace id value.
func EnsureTraceID(ctx context.Context) (context.Context, string) {
	if traceID := TraceIDFromContext(ctx); traceID != "" {
		return ctx, traceID
	}
	traceID := generateTraceID()
	return WithTraceID(ctx, traceID), traceID
}

func generateTraceID() string {
	buf := make([]byte, 8)
	if _, err := rand.Read(buf); err != nil {
		return "trace-fallback"
	}
	return hex.EncodeToString(buf)
}
