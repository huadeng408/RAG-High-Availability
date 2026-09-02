package kafka

import (
	"context"
	"encoding/json"
	"errors"
	"testing"
	"time"

	"github.com/huadeng408/RAG-High-Availability/internal/config"
	"github.com/huadeng408/RAG-High-Availability/pkg/tasks"
	segmentkafka "github.com/segmentio/kafka-go"
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
	stored   *capturedDeadLetter
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

func (s *deadLetterTrackerStub) GetDeadLetterByKey(fileMD5, documentVersion, stage, windowID string) (string, string, error) {
	if s.stored == nil || s.stored.fileMD5 != fileMD5 || s.stored.documentVersion != documentVersion || s.stored.stage != stage || s.stored.windowID != windowID {
		return "", "", nil
	}
	return s.stored.payload, s.stored.messageID, nil
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

func TestHandoffFailedTaskRepublishesPersistedEnvelopeWithoutRecreatingDLQ(t *testing.T) {
	messageID := "ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff"
	payload := `{"file_md5":"0123456789abcdef0123456789abcdef","file_name":"recovery.png","stage":"embed","document_version":"version-sha","window_id":"window-1","dlq_id":"` + messageID + `","attempt":4,"last_error":"stored failure"}`
	tracker := &deadLetterTrackerStub{stored: &capturedDeadLetter{
		fileMD5: "0123456789abcdef0123456789abcdef", documentVersion: "version-sha", stage: "embed", windowID: "window-1", payload: payload, messageID: messageID,
	}}
	var published tasks.FileProcessingTask
	err := handoffFailedTask(
		tracker,
		tasks.FileProcessingTask{FileMD5: "0123456789abcdef0123456789abcdef", FileName: "recovery.png", Stage: tasks.StageEmbed, TaskChunkID: 1},
		"version-sha", "window-1", 4, 3, errors.New("new failure"),
		func(tasks.FileProcessingTask) error {
			t.Fatal("persisted terminal failure must not use retry topic")
			return nil
		},
		func(task tasks.FileProcessingTask) error { published = task; return nil },
	)
	if err != nil {
		t.Fatal(err)
	}
	if len(tracker.captured) != 0 {
		t.Fatalf("persisted DLQ was recreated: %#v", tracker.captured)
	}
	if published.DLQID != messageID || published.LastError != "stored failure" || published.Attempt != 4 {
		t.Fatalf("republished task did not preserve stored envelope: %#v", published)
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

func TestPipelineWindowIDDerivesEmbedWindowFromTaskChunk(t *testing.T) {
	got := pipelineWindowID(tasks.FileProcessingTask{Stage: tasks.StageEmbed, TaskChunkID: 3, WindowID: "window-1"})
	if got != "window-3" {
		t.Fatalf("embed window ID = %q, want window-3", got)
	}
}

func TestMalformedKafkaMessagePublishesToDLQBeforeCommit(t *testing.T) {
	var events []string
	err := handleMalformedMessage([]byte("not-json"), func(payload []byte) error {
		events = append(events, "publish:"+string(payload))
		return nil
	}, func() error {
		events = append(events, "commit")
		return nil
	})
	if err != nil {
		t.Fatal(err)
	}
	if got, want := events, []string{"publish:not-json", "commit"}; len(got) != len(want) || got[0] != want[0] || got[1] != want[1] {
		t.Fatalf("events = %#v, want %#v", got, want)
	}
}

func TestMalformedKafkaMessageDoesNotCommitWhenDLQPublishFails(t *testing.T) {
	committed := false
	err := handleMalformedMessage([]byte("not-json"), func([]byte) error { return errors.New("dlq unavailable") }, func() error {
		committed = true
		return nil
	})
	if err == nil || committed {
		t.Fatalf("err=%v committed=%v, want publish error and no commit", err, committed)
	}
}

func TestSuccessfulKafkaMessageDoesNotCommitWhenStatePersistenceFails(t *testing.T) {
	committed := false
	err := handleSuccessfulMessage(func() error { return errors.New("database unavailable") }, func() error {
		committed = true
		return nil
	})
	if err == nil || committed {
		t.Fatalf("err=%v committed=%v, want persistence error and no commit", err, committed)
	}
}

func TestSerialConsumerDoesNotFetchNextPartitionMessageBeforeCurrentIsDurable(t *testing.T) {
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	messages := []segmentkafka.Message{
		{Partition: 0, Offset: 41, Value: []byte("n")},
		{Partition: 0, Offset: 42, Value: []byte("n+1")},
	}
	fetchCount := 0
	processed := make([]int64, 0, 2)
	committed := make([]int64, 0, 2)
	go func() {
		time.Sleep(20 * time.Millisecond)
		cancel()
	}()

	err := consumeMessagesSerially(
		ctx,
		func(context.Context) (segmentkafka.Message, error) {
			fetchCount++
			if fetchCount > len(messages) {
				return segmentkafka.Message{}, context.Canceled
			}
			return messages[fetchCount-1], nil
		},
		func(ctx context.Context, message segmentkafka.Message) error {
			processed = append(processed, message.Offset)
			if message.Offset == 41 {
				return handleSuccessfulMessage(
					func() error { return errors.New("success persistence unavailable") },
					func() error {
						committed = append(committed, message.Offset)
						return nil
					},
				)
			}
			committed = append(committed, message.Offset)
			return nil
		},
		time.Millisecond,
	)
	if !errors.Is(err, context.Canceled) {
		t.Fatalf("consume err=%v, want context cancellation", err)
	}
	if fetchCount != 1 || len(processed) == 0 {
		t.Fatalf("fetchCount=%d processed=%v, want only message N", fetchCount, processed)
	}
	for _, offset := range processed {
		if offset != 41 {
			t.Fatalf("processed later message offset=%d, all retries must remain at N", offset)
		}
	}
	if len(committed) != 0 {
		t.Fatalf("committed=%v, want no offset committed while durability fails", committed)
	}
}
