package kafka

import (
	"testing"
	"time"

	"github.com/huadeng408/RAG-High-Availability/internal/config"
)

func TestRetryDelayCapsAtFiveSeconds(t *testing.T) {
	if got := retryDelay(800*time.Millisecond, 4); got != 5*time.Second {
		t.Fatalf("retry delay = %s, want 5s", got)
	}
	if got := retryDelay(800*time.Millisecond, 1); got != 800*time.Millisecond {
		t.Fatalf("first retry delay = %s, want 800ms", got)
	}
}

func TestConsumerReaderConfigAcceptsSmallMessages(t *testing.T) {
	got := consumerReaderConfig(config.KafkaConfig{}, "file-parse", "rha-test-parse")

	if got.MinBytes != 1 {
		t.Fatalf("consumer MinBytes = %d, want 1 so a single small task is fetched", got.MinBytes)
	}
	if got.MaxBytes != 10e6 {
		t.Fatalf("consumer MaxBytes = %d, want 10MB", got.MaxBytes)
	}
}
