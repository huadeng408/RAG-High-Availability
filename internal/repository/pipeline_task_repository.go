// Package repository contains data-access code.
package repository

import (
	"errors"
	"fmt"
	"github.com/huadeng408/RAG-High-Availability/internal/model"
	"time"

	"gorm.io/gorm"
	"gorm.io/gorm/clause"
)

// PipelineTaskRepository defines persistence operations for pipeline task data.
type PipelineTaskRepository interface {
	GetOrStart(documentVersion, stage, windowID string) (*model.PipelineTask, error)
	MarkProcessingByKey(documentVersion, stage, windowID string) (*model.PipelineTask, error)
	MarkSuccessByKey(documentVersion, stage, windowID string) error
	MarkRetryByKey(documentVersion, stage, windowID, lastError string) (int, error)
	MarkFailedByKey(documentVersion, stage, windowID, lastError string) error
	GetByKey(fileMD5, stage string, chunkID int) (*model.PipelineTask, error)
	MarkProcessing(fileMD5, stage string, chunkID int) (*model.PipelineTask, error)
	MarkSuccess(fileMD5, stage string, chunkID int) error
	MarkRetry(fileMD5, stage string, chunkID int, lastError string) (int, error)
	MarkFailed(fileMD5, stage string, chunkID int, lastError string) error
	ListFailedByFile(fileMD5 string) ([]model.PipelineTask, error)
	DeleteByFileMD5(fileMD5 string) error
}

// pipelineTaskRepository implements persistence operations for pipeline task data.
type pipelineTaskRepository struct {
	db *gorm.DB
}

// NewPipelineTaskRepository creates a pipeline task repository.
func NewPipelineTaskRepository(db *gorm.DB) PipelineTaskRepository {
	return &pipelineTaskRepository{db: db}
}

// buildPipelineKey builds pipeline key.
func buildPipelineKey(fileMD5, stage string, chunkID int) string {
	return fmt.Sprintf("%s:%s:%d", fileMD5, stage, chunkID)
}

// GetOrStart atomically creates or loads one versioned pipeline task.
func (r *pipelineTaskRepository) GetOrStart(documentVersion, stage, windowID string) (*model.PipelineTask, error) {
	task := &model.PipelineTask{
		DocumentVersion: documentVersion,
		Stage:           stage,
		WindowID:        windowID,
		Status:          model.PipelineStatusPending,
		ChunkID:         -1,
		IdempotencyKey:  fmt.Sprintf("%s:%s:%s", documentVersion, stage, windowID),
	}
	result := r.db.Clauses(clause.OnConflict{
		Columns:   []clause.Column{{Name: "document_version"}, {Name: "stage"}, {Name: "window_id"}},
		DoNothing: true,
	}).Create(task)
	if result.Error != nil {
		return nil, result.Error
	}
	if result.RowsAffected > 0 {
		return task, nil
	}
	if err := r.db.Where("document_version = ? AND stage = ? AND window_id = ?", documentVersion, stage, windowID).First(task).Error; err != nil {
		return nil, err
	}
	return task, nil
}

func (r *pipelineTaskRepository) MarkProcessingByKey(documentVersion, stage, windowID string) (*model.PipelineTask, error) {
	task, err := r.GetOrStart(documentVersion, stage, windowID)
	if err != nil {
		return nil, err
	}
	task.Status = model.PipelineStatusProcessing
	return task, r.db.Save(task).Error
}

func (r *pipelineTaskRepository) MarkSuccessByKey(documentVersion, stage, windowID string) error {
	task, err := r.MarkProcessingByKey(documentVersion, stage, windowID)
	if err != nil {
		return err
	}
	task.Status = model.PipelineStatusSuccess
	task.LastError = ""
	task.NextAttemptAt = nil
	return r.db.Save(task).Error
}

func (r *pipelineTaskRepository) MarkRetryByKey(documentVersion, stage, windowID, lastError string) (int, error) {
	task, err := r.MarkProcessingByKey(documentVersion, stage, windowID)
	if err != nil {
		return 0, err
	}
	task.Status = model.PipelineStatusFailed
	task.RetryCount++
	task.LastError = lastError
	next := time.Now().Add(pipelineRetryBackoff(task.RetryCount))
	task.NextAttemptAt = &next
	return task.RetryCount, r.db.Save(task).Error
}

func (r *pipelineTaskRepository) MarkFailedByKey(documentVersion, stage, windowID, lastError string) error {
	task, err := r.MarkProcessingByKey(documentVersion, stage, windowID)
	if err != nil {
		return err
	}
	task.Status = model.PipelineStatusFailed
	task.LastError = lastError
	return r.db.Save(task).Error
}

// GetByKey returns by key.
func (r *pipelineTaskRepository) GetByKey(fileMD5, stage string, chunkID int) (*model.PipelineTask, error) {
	var task model.PipelineTask
	err := r.db.Where("file_md5 = ? AND stage = ? AND chunk_id = ?", fileMD5, stage, chunkID).First(&task).Error
	if err != nil {
		return nil, err
	}
	return &task, nil
}

// MarkProcessing handles mark processing.
func (r *pipelineTaskRepository) MarkProcessing(fileMD5, stage string, chunkID int) (*model.PipelineTask, error) {
	task, err := r.GetByKey(fileMD5, stage, chunkID)
	if err != nil {
		if !errors.Is(err, gorm.ErrRecordNotFound) {
			return nil, err
		}
		task = &model.PipelineTask{
			FileMD5:         fileMD5,
			DocumentVersion: "upload:" + fileMD5,
			Stage:           stage,
			WindowID:        fmt.Sprintf("%d", chunkID),
			ChunkID:         chunkID,
			Status:          model.PipelineStatusProcessing,
			RetryCount:      0,
			IdempotencyKey:  buildPipelineKey(fileMD5, stage, chunkID),
		}
		return task, r.db.Create(task).Error
	}
	task.Status = model.PipelineStatusProcessing
	return task, r.db.Save(task).Error
}

// MarkSuccess handles mark success.
func (r *pipelineTaskRepository) MarkSuccess(fileMD5, stage string, chunkID int) error {
	task, err := r.MarkProcessing(fileMD5, stage, chunkID)
	if err != nil {
		return err
	}
	task.Status = model.PipelineStatusSuccess
	task.LastError = ""
	return r.db.Save(task).Error
}

// MarkRetry handles mark retry.
func (r *pipelineTaskRepository) MarkRetry(fileMD5, stage string, chunkID int, lastError string) (int, error) {
	task, err := r.MarkProcessing(fileMD5, stage, chunkID)
	if err != nil {
		return 0, err
	}
	task.Status = model.PipelineStatusFailed
	task.RetryCount++
	task.LastError = lastError
	next := time.Now().Add(pipelineRetryBackoff(task.RetryCount))
	task.NextAttemptAt = &next
	return task.RetryCount, r.db.Save(task).Error
}

// MarkFailed handles mark failed.
func (r *pipelineTaskRepository) MarkFailed(fileMD5, stage string, chunkID int, lastError string) error {
	task, err := r.MarkProcessing(fileMD5, stage, chunkID)
	if err != nil {
		return err
	}
	task.Status = model.PipelineStatusFailed
	task.LastError = lastError
	return r.db.Save(task).Error
}

// ListFailedByFile lists failed by file.
func (r *pipelineTaskRepository) ListFailedByFile(fileMD5 string) ([]model.PipelineTask, error) {
	var tasks []model.PipelineTask
	err := r.db.Where("file_md5 = ? AND status = ?", fileMD5, model.PipelineStatusFailed).Order("updated_at desc").Find(&tasks).Error
	return tasks, err
}

// DeleteByFileMD5 deletes all pipeline task rows for one file.
func (r *pipelineTaskRepository) DeleteByFileMD5(fileMD5 string) error {
	return r.db.Where("file_md5 = ?", fileMD5).Delete(&model.PipelineTask{}).Error
}

func pipelineRetryBackoff(retryCount int) time.Duration {
	base := 800 * time.Millisecond
	if retryCount <= 1 {
		return base
	}
	delay := base
	for attempt := 1; attempt < retryCount; attempt++ {
		if delay >= 2500*time.Millisecond {
			return 5 * time.Second
		}
		delay *= 2
	}
	if delay > 5*time.Second {
		return 5 * time.Second
	}
	return delay
}
