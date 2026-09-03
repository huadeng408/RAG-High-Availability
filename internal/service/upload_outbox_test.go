package service

import (
	"context"
	"errors"
	"path/filepath"
	"strings"
	"sync"
	"sync/atomic"
	"testing"
	"time"

	"github.com/glebarez/sqlite"
	"github.com/huadeng408/RAG-High-Availability/internal/model"
	"github.com/huadeng408/RAG-High-Availability/internal/repository"
	rhalog "github.com/huadeng408/RAG-High-Availability/pkg/log"
	"github.com/huadeng408/RAG-High-Availability/pkg/tasks"
	"gorm.io/gorm"
)

func openDurableInitialTaskOutbox(t *testing.T, path string, migrate bool) (repository.PipelineTaskRepository, *gorm.DB) {
	t.Helper()
	dsn := "file:" + filepath.ToSlash(path) + "?_pragma=busy_timeout(5000)&_pragma=journal_mode(WAL)"
	db, err := gorm.Open(sqlite.Open(dsn), &gorm.Config{})
	if err != nil {
		t.Fatal(err)
	}
	if migrate {
		if err := db.AutoMigrate(&model.FileUpload{}, &model.PipelineTask{}); err != nil {
			t.Fatal(err)
		}
	}
	return repository.NewPipelineTaskRepository(db), db
}

func closeTestDatabase(t *testing.T, db *gorm.DB) {
	t.Helper()
	sqlDB, err := db.DB()
	if err != nil {
		t.Fatal(err)
	}
	if err := sqlDB.Close(); err != nil {
		t.Fatal(err)
	}
}

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

type signalingInitialTaskOutbox struct {
	initialTaskOutbox
	initialDrainComplete chan struct{}
	once                 sync.Once
}

func (s *signalingInitialTaskOutbox) ClaimPendingInitialTasks(
	ctx context.Context,
	limit int,
	lease time.Duration,
) ([]model.PipelineTask, error) {
	rows, err := s.initialTaskOutbox.ClaimPendingInitialTasks(ctx, limit, lease)
	s.once.Do(func() { close(s.initialDrainComplete) })
	return rows, err
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
	if published[0].DocumentVersion != "" || published[0].TraceID != "trace-upload" {
		t.Fatalf("published parse task must defer immutable version creation: %#v", published[0])
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
		if got.DocumentVersion != "" || attempts != 2 {
			t.Fatalf("published task=%#v attempts=%d", got, attempts)
		}
	case <-time.After(time.Second):
		t.Fatal("dispatcher did not automatically drain after broker recovery")
	}
}

func TestInitialTaskDispatcherWaitsFreshIntervalAfterSlowPublishFailure(t *testing.T) {
	rhalog.Init("error", "console", "")
	outbox, _, upload := newInitialTaskOutbox(t)
	signalingOutbox := &signalingInitialTaskOutbox{
		initialTaskOutbox:    outbox,
		initialDrainComplete: make(chan struct{}),
	}
	const interval = 40 * time.Millisecond
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	firstStarted := make(chan struct{})
	releaseFirst := make(chan struct{})
	secondDelay := make(chan time.Duration, 1)
	attempts := 0
	var firstFailedAt time.Time
	go runInitialTaskDispatcher(ctx, signalingOutbox, func(tasks.FileProcessingTask) error {
		attempts++
		if attempts == 1 {
			close(firstStarted)
			<-releaseFirst
			firstFailedAt = time.Now()
			return errors.New("broker unavailable")
		}
		secondDelay <- time.Since(firstFailedAt)
		return nil
	}, interval)

	select {
	case <-signalingOutbox.initialDrainComplete:
	case <-time.After(time.Second):
		t.Fatal("dispatcher startup drain did not complete")
	}
	task := tasks.FileProcessingTask{FileMD5: upload.FileMD5, FileName: upload.FileName, Stage: tasks.StageParse}
	if err := outbox.CompleteUploadAndEnqueueInitialTask(upload.ID, task); err != nil {
		t.Fatal(err)
	}
	select {
	case <-firstStarted:
	case <-time.After(time.Second):
		t.Fatal("initial publish attempt did not start")
	}
	time.Sleep(3 * interval)
	close(releaseFirst)

	select {
	case delay := <-secondDelay:
		if delay < interval {
			t.Fatalf("second claim started %v after slow failure, want at least %v", delay, interval)
		}
		if attempts != 2 {
			t.Fatalf("publish attempts = %d, want 2", attempts)
		}
	case <-time.After(time.Second):
		t.Fatal("dispatcher did not automatically recover after fresh retry interval")
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

func TestInitialTaskOutboxRecoversStaleClaimAfterProcessRestart(t *testing.T) {
	databasePath := filepath.Join(t.TempDir(), "outbox.db")
	firstOutbox, firstDB := openDurableInitialTaskOutbox(t, databasePath, true)
	upload := model.FileUpload{FileMD5: strings.Repeat("b", 32), FileName: "restart.pdf", TotalSize: 12, UserID: 9, OrgTag: "org-a"}
	if err := firstDB.Create(&upload).Error; err != nil {
		t.Fatal(err)
	}
	task := tasks.FileProcessingTask{FileMD5: upload.FileMD5, FileName: upload.FileName, Stage: tasks.StageParse}
	if err := firstOutbox.CompleteUploadAndEnqueueInitialTask(upload.ID, task); err != nil {
		t.Fatal(err)
	}
	claimed, err := firstOutbox.ClaimPendingInitialTasks(context.Background(), 1, time.Minute)
	if err != nil || len(claimed) != 1 {
		t.Fatalf("initial claim = %#v, err=%v", claimed, err)
	}
	closeTestDatabase(t, firstDB)

	secondOutbox, secondDB := openDurableInitialTaskOutbox(t, databasePath, false)
	t.Cleanup(func() { closeTestDatabase(t, secondDB) })
	staleAt := time.Now().Add(-2 * time.Minute)
	if err := secondDB.Model(&model.PipelineTask{}).Where("id = ?", claimed[0].ID).Update("publication_claimed_at", staleAt).Error; err != nil {
		t.Fatal(err)
	}
	published := 0
	count, err := dispatchInitialTasksOnce(context.Background(), secondOutbox, func(tasks.FileProcessingTask) error {
		published++
		return nil
	}, 1, time.Minute)
	if err != nil {
		t.Fatal(err)
	}
	if count != 1 || published != 1 {
		t.Fatalf("restart published count=%d calls=%d", count, published)
	}
	var stored model.PipelineTask
	if err := secondDB.First(&stored, claimed[0].ID).Error; err != nil {
		t.Fatal(err)
	}
	if stored.PublicationStatus != model.PipelinePublicationPublished || stored.PublicationAttemptCount != 2 {
		t.Fatalf("recovered publication metadata = %#v", stored)
	}
}

func TestInitialTaskOutboxConcurrentDispatchersPublishOnceAcrossInstances(t *testing.T) {
	databasePath := filepath.Join(t.TempDir(), "outbox.db")
	firstOutbox, firstDB := openDurableInitialTaskOutbox(t, databasePath, true)
	secondOutbox, secondDB := openDurableInitialTaskOutbox(t, databasePath, false)
	t.Cleanup(func() { closeTestDatabase(t, firstDB) })
	t.Cleanup(func() { closeTestDatabase(t, secondDB) })
	upload := model.FileUpload{FileMD5: strings.Repeat("c", 32), FileName: "contended.pdf", TotalSize: 12, UserID: 10, OrgTag: "org-a"}
	if err := firstDB.Create(&upload).Error; err != nil {
		t.Fatal(err)
	}
	if err := firstOutbox.CompleteUploadAndEnqueueInitialTask(upload.ID, tasks.FileProcessingTask{FileMD5: upload.FileMD5, FileName: upload.FileName, Stage: tasks.StageParse}); err != nil {
		t.Fatal(err)
	}

	queryReady := make(chan struct{}, 2)
	releaseQueries := make(chan struct{})
	registerBarrier := func(db *gorm.DB, name string) {
		t.Helper()
		if err := db.Callback().Query().After("gorm:query").Register(name, func(tx *gorm.DB) {
			if tx.Statement.Table == "pipeline_task" {
				queryReady <- struct{}{}
				<-releaseQueries
			}
		}); err != nil {
			t.Fatal(err)
		}
	}
	registerBarrier(firstDB, "test:first-claim-barrier")
	registerBarrier(secondDB, "test:second-claim-barrier")

	type dispatchResult struct {
		count int
		err   error
	}
	results := make(chan dispatchResult, 2)
	start := make(chan struct{})
	var publishCalls int32
	for _, outbox := range []repository.PipelineTaskRepository{firstOutbox, secondOutbox} {
		go func(candidate repository.PipelineTaskRepository) {
			<-start
			count, err := dispatchInitialTasksOnce(context.Background(), candidate, func(tasks.FileProcessingTask) error {
				atomic.AddInt32(&publishCalls, 1)
				return nil
			}, 1, time.Minute)
			results <- dispatchResult{count: count, err: err}
		}(outbox)
	}
	close(start)
	<-queryReady
	<-queryReady
	close(releaseQueries)

	total := 0
	for range 2 {
		result := <-results
		if result.err != nil {
			t.Fatalf("concurrent dispatcher error: %v", result.err)
		}
		total += result.count
	}
	if total != 1 || atomic.LoadInt32(&publishCalls) != 1 {
		t.Fatalf("concurrent dispatch count=%d publish calls=%d, want one", total, publishCalls)
	}
}

func TestInitialTaskOutboxRepublishesOnlyAfterPublishBeforeAckCrashWindow(t *testing.T) {
	databasePath := filepath.Join(t.TempDir(), "outbox.db")
	firstOutbox, firstDB := openDurableInitialTaskOutbox(t, databasePath, true)
	upload := model.FileUpload{FileMD5: strings.Repeat("d", 32), FileName: "at-least-once.pdf", TotalSize: 12, UserID: 11, OrgTag: "org-a"}
	if err := firstDB.Create(&upload).Error; err != nil {
		t.Fatal(err)
	}
	if err := firstOutbox.CompleteUploadAndEnqueueInitialTask(upload.ID, tasks.FileProcessingTask{FileMD5: upload.FileMD5, FileName: upload.FileName, Stage: tasks.StageParse}); err != nil {
		t.Fatal(err)
	}
	claimed, err := firstOutbox.ClaimPendingInitialTasks(context.Background(), 1, time.Minute)
	if err != nil || len(claimed) != 1 {
		t.Fatalf("initial claim = %#v, err=%v", claimed, err)
	}
	publishCalls := 1 // Kafka acknowledged, then the process exited before MarkInitialTaskPublished.
	closeTestDatabase(t, firstDB)

	secondOutbox, secondDB := openDurableInitialTaskOutbox(t, databasePath, false)
	t.Cleanup(func() { closeTestDatabase(t, secondDB) })
	if err := secondDB.Model(&model.PipelineTask{}).Where("id = ?", claimed[0].ID).Update("publication_claimed_at", time.Now().Add(-2*time.Minute)).Error; err != nil {
		t.Fatal(err)
	}
	count, err := dispatchInitialTasksOnce(context.Background(), secondOutbox, func(tasks.FileProcessingTask) error {
		publishCalls++
		return nil
	}, 1, time.Minute)
	if err != nil {
		t.Fatal(err)
	}
	if count != 1 || publishCalls != 2 {
		t.Fatalf("crash-window dispatch count=%d publish calls=%d, want one retry and two total deliveries", count, publishCalls)
	}
}
