package service

import (
	"errors"
	"strings"
	"testing"

	"github.com/huadeng408/RAG-High-Availability/pkg/tasks"
)

type initialOutboxStub struct {
	tasks []tasks.FileProcessingTask
}

func (s *initialOutboxStub) EnqueueInitialTask(task tasks.FileProcessingTask) error {
	s.tasks = append(s.tasks, task)
	return nil
}

func TestInitialTaskIsDurableBeforePublishAndPublishFailureIsReturned(t *testing.T) {
	outbox := &initialOutboxStub{}
	service := &uploadService{
		initialOutbox: outbox,
		produceTask: func(tasks.FileProcessingTask) error {
			if len(outbox.tasks) != 1 {
				t.Fatal("producer ran before durable outbox write")
			}
			return errors.New("broker unavailable")
		},
	}
	task := tasks.FileProcessingTask{FileMD5: strings.Repeat("a", 32), DocumentVersion: "upload:" + strings.Repeat("a", 32), Stage: tasks.StageParse, TraceID: "trace-upload"}
	err := service.dispatchInitialTask(task)
	if err == nil || !strings.Contains(err.Error(), "broker unavailable") {
		t.Fatalf("dispatch error = %v", err)
	}
	if len(outbox.tasks) != 1 || outbox.tasks[0].TraceID != "trace-upload" {
		t.Fatalf("durable task = %#v", outbox.tasks)
	}
}

func TestInitialTaskCanBeRepublishedFromSameDurableIdentity(t *testing.T) {
	outbox := &initialOutboxStub{}
	attempts := 0
	service := &uploadService{initialOutbox: outbox, produceTask: func(tasks.FileProcessingTask) error {
		attempts++
		if attempts == 1 {
			return errors.New("temporary failure")
		}
		return nil
	}}
	task := tasks.FileProcessingTask{FileMD5: strings.Repeat("b", 32), DocumentVersion: "upload:" + strings.Repeat("b", 32), Stage: tasks.StageParse}
	if err := service.dispatchInitialTask(task); err == nil {
		t.Fatal("first publish unexpectedly succeeded")
	}
	if err := service.dispatchInitialTask(task); err != nil {
		t.Fatal(err)
	}
	if attempts != 2 || len(outbox.tasks) != 2 {
		t.Fatalf("attempts=%d durable writes=%d", attempts, len(outbox.tasks))
	}
}
