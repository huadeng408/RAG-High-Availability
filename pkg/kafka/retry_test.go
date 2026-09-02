package kafka

import (
	"encoding/json"
	"errors"
	"testing"
	"time"

	"github.com/huadeng408/RAG-High-Availability/internal/config"
	"github.com/huadeng408/RAG-High-Availability/pkg/tasks"
)

type capturedDeadLetter struct {
	fileMD5         string
	documentVersion string
	stage           string
	windowID        string
	lastError       string
	payload         string
	messageID       string
}

type deadLetterTrackerStub struct {
	captured []capturedDeadLetter
	err      error
}

func (s *deadLetterTrackerStub) MarkDeadLetterByKey(
	fileMD5, documentVersion, stage, windowID, lastError, payload, messageID string,
) error {
	s.captured = append(s.captured, capturedDeadLetter{
		fileMD5:         fileMD5,
		documentVersion: documentVersion,
		stage:           stage,
		windowID:        windowID,
		lastError:       lastError,
		payload:         payload,
		messageID:       messageID,
	})
	return s.err
}

func TestRetryDelayCapsAtFiveSeconds(t *testing.T) {
	if got := retryDelay(800*time.Millisecond, 4); got != 5*time.Second {
		t.Fatalf("retry delay = %s, want 5s", got)
	}
	if got := retryDelay(800*time.Millisecond, 1); got != 800*time.Millisecond {
		t.Fatalf("first retry delay = %s, want 800ms", got)
	}
}

func TestBuildDeadLetterTaskPreservesVersionedIdentityAndStableEnvelope(t *testing.T) {
	input := tasks.FileProcessingTask{
		FileMD5:     "0123456789abcdef0123456789abcdef",
		FileName:    "recovery.png",
		Stage:       tasks.StageEmbed,
		TaskChunkID: 2,
		ChunkStart:  256,
		TotalChunks: 300,
	}

	first, firstPayload, err := buildDeadLetterTask(
		input,
		"version-sha",
		"window-2",
		3,
		"embedding unavailable",
	)
	if err != nil {
		t.Fatal(err)
	}
	second, secondPayload, err := buildDeadLetterTask(
		input,
		"version-sha",
		"window-2",
		3,
		"embedding unavailable",
	)
	if err != nil {
		t.Fatal(err)
	}
	if first.DLQID == "" || len(first.DLQID) != 64 || first.DLQID != second.DLQID {
		t.Fatalf("unstable DLQ message ID: %q %q", first.DLQID, second.DLQID)
	}
	if string(firstPayload) != string(secondPayload) {
		t.Fatalf("unstable DLQ payloads: %s != %s", firstPayload, secondPayload)
	}
	if first.DocumentVersion != "version-sha" || first.WindowID != "window-2" || first.Attempt != 3 {
		t.Fatalf("dead-letter identity not preserved: %#v", first)
	}
	if first.LastError != "embedding unavailable" {
		t.Fatalf("last error = %q", first.LastError)
	}
	var decoded tasks.FileProcessingTask
	if err := json.Unmarshal(firstPayload, &decoded); err != nil {
		t.Fatal(err)
	}
	if decoded != first {
		t.Fatalf("persisted payload %#v does not match published task %#v", decoded, first)
	}
}

func TestHandoffFailedTaskPersistsExactEnvelopeBeforeDLQPublish(t *testing.T) {
	tracker := &deadLetterTrackerStub{}
	var published tasks.FileProcessingTask
	task := tasks.FileProcessingTask{
		FileMD5:     "0123456789abcdef0123456789abcdef",
		FileName:    "recovery.png",
		Stage:       tasks.StageEmbed,
		TaskChunkID: 1,
		TotalChunks: 1,
	}

	err := handoffFailedTask(
		tracker,
		task,
		"version-sha",
		"window-1",
		3,
		2,
		errors.New("embedding unavailable"),
		func(tasks.FileProcessingTask) error { t.Fatal("terminal failure must not use retry topic"); return nil },
		func(task tasks.FileProcessingTask) error { published = task; return nil },
	)
	if err != nil {
		t.Fatal(err)
	}
	if len(tracker.captured) != 1 {
		t.Fatalf("persist calls = %d, want 1", len(tracker.captured))
	}
	persisted := tracker.captured[0]
	if persisted.documentVersion != "version-sha" || persisted.windowID != "window-1" || persisted.stage != "embed" {
		t.Fatalf("persisted wrong identity: %#v", persisted)
	}
	if persisted.messageID == "" || persisted.messageID != published.DLQID {
		t.Fatalf("persisted message ID %q != published %q", persisted.messageID, published.DLQID)
	}
	var persistedTask tasks.FileProcessingTask
	if err := json.Unmarshal([]byte(persisted.payload), &persistedTask); err != nil {
		t.Fatal(err)
	}
	if persistedTask != published {
		t.Fatalf("persisted task %#v != published task %#v", persistedTask, published)
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
