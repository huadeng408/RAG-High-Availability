package observability

import (
	"context"
	"testing"
)

func TestTraceIDSurvivesSpanBoundaries(t *testing.T) {
	ctx := WithTraceID(context.Background(), "trace-9")
	child, span := StartSpan(ctx, "pipeline.parse")
	defer span.End()
	if got := TraceID(child); got != "trace-9" {
		t.Fatalf("trace id = %q, want trace-9", got)
	}
	if span.Name != "pipeline.parse" {
		t.Fatalf("span name = %q", span.Name)
	}
}
