package service

import (
	"context"
	"errors"
	"strings"
	"testing"
	"time"

	"github.com/glebarez/sqlite"
	"github.com/huadeng408/RAG-High-Availability/internal/model"
	"github.com/huadeng408/RAG-High-Availability/internal/repository"
	rhalog "github.com/huadeng408/RAG-High-Availability/pkg/log"
	"github.com/huadeng408/RAG-High-Availability/pkg/tasks"
	"gorm.io/gorm"
)

func newInitialTaskOutbox(t *testing.T) (repository.PipelineTaskRepository, *gorm.DB, model.FileUpload) {
	t.Helper()
	db, err := gorm.Open(sqlite.Open("file:"+t.Name()+"?mode=memory&cache=shared"), &gorm.Config{})
	if err != nil {
		t.Fatal(err)
	}
	if err := db.AutoMigrate(&model.FileUpload{}, &model.PipelineTask{}); err != nil {
		t.Fatal(err)
	}
	upload := model.FileUpload{
		FileMD5: strings.Repeat("a", 32), FileName: "restart.pdf", TotalSize: 12,
		Status: 0, UserID: 7, OrgTag: "org-a",
	}
	if err := db.Create(&upload).Error; err != nil {
		t.Fatal(err)
	}
	return repository.NewPipelineTaskRepository(db), db, upload
}

func TestInitialTaskOutboxRepublishesAfterDispatcherRestartWithoutSecondMerge(t *testing.T) {
	outbox, db, upload := newInitialTaskOutbox(t)
	task := tasks.FileProcessingTask{
		FileMD5: upload.FileMD5, FileName: upload.FileName, Stage: tasks.StageParse,
		TraceID: "trace-upload",
	}
	if err := outbox.CompleteUploadAndEnqueueInitialTask(upload.ID, task); err != nil {
		t.Fatal(err)
	}

	_, err := dispatchInitialTasksOnce(context.Background(), outbox, func(tasks.FileProcessingTask) error {
		return errors.New("broker unavailable")
	}, 10, time.Minute)
	if err == nil || !strings.Contains(err.Error(), "broker unavailable") {
		t.Fatalf("first dispatcher error = %v", err)
	}

	var published []tasks.FileProcessingTask
	count, err := dispatchInitialTasksOnce(context.Background(), outbox, func(task tasks.FileProcessingTask) error {
		published = append(published, task)
		return nil
	}, 10, time.Minute)
	if err != nil {
		t.Fatal(err)
	}
	if count != 1 || len(published) != 1 {
		t.Fatalf("published count=%d tasks=%#v", count, published)
	}
	if published[0].DocumentVersion != "upload:"+upload.FileMD5 || published[0].TraceID != "trace-upload" {
		t.Fatalf("durable publication identity = %#v", published[0])
	}

	var storedUpload model.FileUpload
	if err := db.First(&storedUpload, upload.ID).Error; err != nil {
		t.Fatal(err)
	}
	if storedUpload.Status != 1 {
		t.Fatalf("upload status = %d, want completed", storedUpload.Status)
	}
	var storedTask model.PipelineTask
	if err := db.Where("document_version = ? AND stage = ? AND window_id = ?", "upload:"+upload.FileMD5, "parse", "root").First(&storedTask).Error; err != nil {
		t.Fatal(err)
	}
	if storedTask.PublicationStatus != model.PipelinePublicationPublished || storedTask.PublicationAttemptCount != 2 {
		t.Fatalf("publication metadata = %#v", storedTask)
	}
}

func TestInitialTaskDispatcherAutomaticallyDrainsAfterBrokerRecovery(t *testing.T) {
	rhalog.Init("error", "console", "")
	outbox, _, upload := newInitialTaskOutbox(t)
	task := tasks.FileProcessingTask{FileMD5: upload.FileMD5, FileName: upload.FileName, Stage: tasks.StageParse}
	if err := outbox.CompleteUploadAndEnqueueInitialTask(upload.ID, task); err != nil {
		t.Fatal(err)
	}

	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	published := make(chan tasks.FileProcessingTask, 1)
	attempts := 0
	go runInitialTaskDispatcher(ctx, outbox, func(task tasks.FileProcessingTask) error {
		attempts++
		if attempts == 1 {
			return errors.New("broker unavailable")
		}
		published <- task
		return nil
	}, 5*time.Millisecond)

	select {
	case got := <-published:
		if got.DocumentVersion != "upload:"+upload.FileMD5 || attempts != 2 {
			t.Fatalf("published task=%#v attempts=%d", got, attempts)
		}
	case <-time.After(time.Second):
		t.Fatal("dispatcher did not automatically drain after broker recovery")
	}
}

func TestUploadCompletionRollsBackWhenInitialTaskCannotBeEnqueued(t *testing.T) {
	outbox, db, upload := newInitialTaskOutbox(t)
	task := tasks.FileProcessingTask{FileMD5: upload.FileMD5, FileName: upload.FileName, Stage: tasks.StageParse}
	if err := outbox.CompleteUploadAndEnqueueInitialTask(upload.ID+100, task); err == nil {
		t.Fatal("missing upload record unexpectedly committed outbox task")
	}

	var count int64
	if err := db.Model(&model.PipelineTask{}).Count(&count).Error; err != nil {
		t.Fatal(err)
	}
	if count != 0 {
		t.Fatalf("outbox rows = %d after transaction rollback", count)
	}
}
