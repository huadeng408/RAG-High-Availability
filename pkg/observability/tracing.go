// Package observability provides lightweight trace propagation shared by the
// gateway and pipeline. It remains useful without an OTLP collector configured.
package observability

import (
	"context"
	"crypto/rand"
	"encoding/hex"
	"os"
	"sync"
	"time"
)

type traceIDKey struct{}

// Span records a stage boundary. When OTLP is configured, callers can export
// this record from the same process without changing the pipeline contract.
type Span struct {
	Name       string
	TraceID    string
	StartedAt  time.Time
	EndedAt    time.Time
	Attributes map[string]string
}

var (
	spansMu sync.Mutex
	spans   []Span
)

func WithTraceID(ctx context.Context, traceID string) context.Context {
	if traceID == "" {
		traceID = newTraceID()
	}
	return context.WithValue(ctx, traceIDKey{}, traceID)
}

func TraceID(ctx context.Context) string {
	if value, ok := ctx.Value(traceIDKey{}).(string); ok && value != "" {
		return value
	}
	return ""
}

func StartSpan(ctx context.Context, name string) (context.Context, *Span) {
	traceID := TraceID(ctx)
	if traceID == "" {
		traceID = newTraceID()
		ctx = WithTraceID(ctx, traceID)
	}
	return ctx, &Span{Name: name, TraceID: traceID, StartedAt: time.Now(), Attributes: map[string]string{}}
}

func (s *Span) SetAttribute(key, value string) {
	if s == nil {
		return
	}
	if s.Attributes == nil {
		s.Attributes = map[string]string{}
	}
	s.Attributes[key] = value
}

func (s *Span) End() {
	if s == nil || !s.EndedAt.IsZero() {
		return
	}
	s.EndedAt = time.Now()
	if os.Getenv("OTEL_EXPORTER_OTLP_ENDPOINT") == "" {
		return
	}
	spansMu.Lock()
	spans = append(spans, *s)
	if len(spans) > 1024 {
		spans = spans[len(spans)-1024:]
	}
	spansMu.Unlock()
}

func newTraceID() string {
	buf := make([]byte, 8)
	if _, err := rand.Read(buf); err != nil {
		return hex.EncodeToString([]byte(time.Now().String()))[:16]
	}
	return hex.EncodeToString(buf)
}
