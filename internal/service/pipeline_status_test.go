package service

import (
	"testing"
	"time"

	"github.com/huadeng408/RAG-High-Availability/internal/model"
)

func TestAggregatePipelineStatusRequiresEveryStageForSearchable(t *testing.T) {
	now := time.Now()
	tasks := []model.PipelineTask{
		{FileMD5: "file-1", DocumentVersion: "version-1", Stage: "parse", WindowID: "root", Status: model.PipelineStatusSuccess, CreatedAt: now},
		{FileMD5: "file-1", DocumentVersion: "version-1", Stage: "chunk", WindowID: "root", Status: model.PipelineStatusSuccess, CreatedAt: now},
		{FileMD5: "file-1", DocumentVersion: "version-1", Stage: "embed", WindowID: "window-1", Status: model.PipelineStatusSuccess, CreatedAt: now},
		{FileMD5: "file-1", DocumentVersion: "version-1", Stage: "embed", WindowID: "window-2", Status: model.PipelineStatusSuccess, CreatedAt: now},
		{FileMD5: "file-1", DocumentVersion: "version-1", Stage: "index", WindowID: "root", Status: model.PipelineStatusSuccess, CreatedAt: now},
	}

	status := AggregatePipelineStatus("file-1", tasks)
	if status.Status != PipelineStatusSearchable {
		t.Fatalf("overall status = %q, want %q", status.Status, PipelineStatusSearchable)
	}
	if status.DocumentVersion != "version-1" {
		t.Fatalf("document version = %q, want version-1", status.DocumentVersion)
	}
	if len(status.Stages) != 4 {
		t.Fatalf("stage count = %d, want 4", len(status.Stages))
	}
	if status.Stages[2].AttemptCount != 2 {
		t.Fatalf("embed attempt count = %d, want 2 windows", status.Stages[2].AttemptCount)
	}
}

func TestAggregatePipelineStatusLinksUploadParseToContentVersion(t *testing.T) {
	parsedAt := time.Now()
	versionedAt := parsedAt.Add(time.Second)
	tasks := []model.PipelineTask{
		{FileMD5: "file-1", DocumentVersion: "upload:file-1", Stage: "parse", WindowID: "root", Status: model.PipelineStatusSuccess, CreatedAt: parsedAt},
		{FileMD5: "file-1", DocumentVersion: "version-sha", Stage: "chunk", WindowID: "root", Status: model.PipelineStatusSuccess, CreatedAt: versionedAt},
		{FileMD5: "file-1", DocumentVersion: "version-sha", Stage: "embed", WindowID: "window-1", Status: model.PipelineStatusSuccess, CreatedAt: versionedAt},
		{FileMD5: "file-1", DocumentVersion: "version-sha", Stage: "index", WindowID: "root", Status: model.PipelineStatusSuccess, CreatedAt: versionedAt},
	}

	status := AggregatePipelineStatus("file-1", tasks)
	if status.Status != PipelineStatusSearchable {
		t.Fatalf("overall status = %q, want %q", status.Status, PipelineStatusSearchable)
	}
	if status.DocumentVersion != "version-sha" {
		t.Fatalf("document version = %q, want version-sha", status.DocumentVersion)
	}
	if status.Stages[0].Status != model.PipelineStatusSuccess {
		t.Fatalf("parse status = %q, want SUCCESS", status.Stages[0].Status)
	}
}

func TestAggregatePipelineStatusPreservesFailureAndRetryMetadata(t *testing.T) {
	next := time.Now().Add(time.Minute)
	deadLetteredAt := time.Now().Add(-time.Minute)
	lastReplayedAt := time.Now().Add(-30 * time.Second)
	tasks := []model.PipelineTask{
		{
			FileMD5: "file-2", DocumentVersion: "version-2", Stage: "parse", WindowID: "root",
			Status: model.PipelineStatusFailed, RetryCount: 2, LastError: "mineru unavailable", NextAttemptAt: &next,
			DLQMessageID: "dddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd",
			DLQPayload:   "must-not-leak", DeadLetteredAt: &deadLetteredAt, ReplayCount: 1, LastReplayedAt: &lastReplayedAt,
		},
	}

	status := AggregatePipelineStatus("file-2", tasks)
	if status.Status != model.PipelineStatusFailed {
		t.Fatalf("overall status = %q, want FAILED", status.Status)
	}
	if status.Stages[0].RetryCount != 2 || status.Stages[0].LastError != "mineru unavailable" {
		t.Fatalf("failure metadata not preserved: %#v", status.Stages[0])
	}
	if status.Stages[0].NextAttemptAt == nil {
		t.Fatal("next attempt timestamp was dropped")
	}
	stage := status.Stages[0]
	if stage.DLQMessageID == "" || stage.DeadLetteredAt == nil || stage.ReplayCount != 1 || stage.LastReplayedAt == nil {
		t.Fatalf("dead-letter replay metadata not preserved: %#v", stage)
	}
}

func TestAggregatePipelineStatusWithoutTasksIsPending(t *testing.T) {
	status := AggregatePipelineStatus("file-3", nil)
	if status.Status != model.PipelineStatusPending {
		t.Fatalf("overall status = %q, want PENDING", status.Status)
	}
	if len(status.Stages) != 4 {
		t.Fatalf("stage count = %d, want 4", len(status.Stages))
	}
}
