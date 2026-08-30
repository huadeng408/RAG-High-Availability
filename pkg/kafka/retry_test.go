package kafka

import (
	"testing"
	"time"
)

func TestRetryDelayCapsAtFiveSeconds(t *testing.T) {
	if got := retryDelay(800*time.Millisecond, 4); got != 5*time.Second {
		t.Fatalf("retry delay = %s, want 5s", got)
	}
	if got := retryDelay(800*time.Millisecond, 1); got != 800*time.Millisecond {
		t.Fatalf("first retry delay = %s, want 800ms", got)
	}
}
