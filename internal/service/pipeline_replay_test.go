package service

import (
	"errors"
	"strings"
	"testing"
	"time"

	"github.com/huadeng408/RAG-High-Availability/internal/model"
	"github.com/huadeng408/RAG-High-Availability/internal/repository"
	"github.com/huadeng408/RAG-High-Availability/pkg/tasks"
)

type replayPipelineRepository struct {
	repository.PipelineTaskRepository
	failed    []model.PipelineTask
	resetKeys []string
	restored  []string
}

func (r *replayPipelineRepository) ListFailedByFile(string) ([]model.PipelineTask, error) {
	return append([]model.PipelineTask(nil), r.failed...), nil
}

func (r *replayPipelineRepository) ResetForReplayByKey(fileMD5, documentVersion, stage, windowID string) error {
	r.resetKeys = append(r.resetKeys, fileMD5+":"+documentVersion+":"+stage+":"+windowID)
	return nil
}

func (r *replayPipelineRepository) MarkDeadLetterByKey(
	fileMD5, documentVersion, stage, windowID, lastError, payload, messageID string,
) error {
	r.restored = append(
		r.restored,
		fileMD5+":"+documentVersion+":"+stage+":"+windowID+":"+lastError+":"+payload+":"+messageID,
	)
	return nil
}

type replayUploadRepository struct {
	repository.UploadRepository
	record *model.FileUpload
}

func (r replayUploadRepository) GetFileUploadRecordByMD5(string) (*model.FileUpload, error) {
	return r.record, nil
}

func TestReplayPipelineTaskPublishesPersistedVersionedDLQEnvelope(t *testing.T) {
	now := time.Now()
	messageID := "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
	payload := `{"file_md5":"0123456789abcdef0123456789abcdef","document_version":"version-sha","window_id":"window-2","file_name":"recovery.png","user_id":7,"org_tag":"ops","stage":"embed","task_chunk_id":2,"chunk_start":256,"total_chunks":300,"last_error":"embedding unavailable","dlq_id":"` + messageID + `"}`
	pipelineRepo := &replayPipelineRepository{failed: []model.PipelineTask{
		{
			FileMD5:         "0123456789abcdef0123456789abcdef",
			DocumentVersion: "version-sha",
			Stage:           "embed",
			WindowID:        "window-2",
			Status:          model.PipelineStatusFailed,
			DLQMessageID:    messageID,
			DLQPayload:      payload,
			DeadLetteredAt:  &now,
		},
	}}
	var produced []tasks.FileProcessingTask
	service := &adminService{
		pipelineTaskRepo: pipelineRepo,
		uploadRepo: replayUploadRepository{record: &model.FileUpload{
			FileMD5:  "0123456789abcdef0123456789abcdef",
			FileName: "recovery.png",
			UserID:   7,
			OrgTag:   "ops",
		}},
		produceTask: func(task tasks.FileProcessingTask) error {
			produced = append(produced, task)
			return nil
		},
	}

	result, err := service.ReplayPipelineTask("0123456789abcdef0123456789abcdef", tasks.StageEmbed)
	if err != nil {
		t.Fatal(err)
	}
	if result.ReplayedTasks != 1 || len(result.MessageIDs) != 1 || result.MessageIDs[0] != messageID {
		t.Fatalf("unexpected replay result: %#v", result)
	}
	if len(produced) != 1 {
		t.Fatalf("produced tasks = %d, want 1", len(produced))
	}
	got := produced[0]
	if got.DocumentVersion != "version-sha" || got.WindowID != "window-2" || got.TaskChunkID != 2 {
		t.Fatalf("versioned task identity was not preserved: %#v", got)
	}
	if got.ChunkStart != 256 || got.TotalChunks != 300 || got.Stage != tasks.StageEmbed {
		t.Fatalf("original embed window was not preserved: %#v", got)
	}
	if got.LastError != "" || got.DLQID != "" {
		t.Fatalf("replayed task retained dead-letter-only fields: %#v", got)
	}
	wantReset := "0123456789abcdef0123456789abcdef:version-sha:embed:window-2"
	if len(pipelineRepo.resetKeys) != 1 || pipelineRepo.resetKeys[0] != wantReset {
		t.Fatalf("reset keys = %#v, want %q", pipelineRepo.resetKeys, wantReset)
	}
}

func TestReplayPipelineTaskRestoresDeadLetterWhenKafkaPublishFails(t *testing.T) {
	now := time.Now()
	messageID := "cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc"
	payload := `{"file_md5":"0123456789abcdef0123456789abcdef","document_version":"version-sha","window_id":"window-1","file_name":"recovery.png","stage":"embed","dlq_id":"` + messageID + `"}`
	pipelineRepo := &replayPipelineRepository{failed: []model.PipelineTask{
		{
			FileMD5:         "0123456789abcdef0123456789abcdef",
			DocumentVersion: "version-sha",
			Stage:           "embed",
			WindowID:        "window-1",
			Status:          model.PipelineStatusFailed,
			DLQMessageID:    messageID,
			DLQPayload:      payload,
			DeadLetteredAt:  &now,
		},
	}}
	service := &adminService{
		pipelineTaskRepo: pipelineRepo,
		uploadRepo: replayUploadRepository{record: &model.FileUpload{
			FileMD5:  "0123456789abcdef0123456789abcdef",
			FileName: "recovery.png",
		}},
		produceTask: func(tasks.FileProcessingTask) error { return errors.New("broker unavailable") },
	}

	result, err := service.ReplayPipelineTask("0123456789abcdef0123456789abcdef", tasks.StageEmbed)
	if err == nil || result == nil {
		t.Fatalf("result=%#v err=%v, want partial result and publish error", result, err)
	}
	if len(pipelineRepo.resetKeys) != 1 {
		t.Fatalf("reset calls = %#v, want one", pipelineRepo.resetKeys)
	}
	if len(pipelineRepo.restored) != 1 {
		t.Fatalf("restored dead letters = %#v, want one", pipelineRepo.restored)
	}
	if got := pipelineRepo.restored[0]; !strings.Contains(got, "replay publish failed: broker unavailable") || !strings.Contains(got, messageID) {
		t.Fatalf("restored envelope = %q", got)
	}
}
