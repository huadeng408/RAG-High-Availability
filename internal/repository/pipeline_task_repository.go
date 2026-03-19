package repository

import (
	"errors"
	"fmt"
	"pai-smart-go/internal/model"

	"gorm.io/gorm"
)

type PipelineTaskRepository interface {
	GetByKey(fileMD5, stage string, chunkID int) (*model.PipelineTask, error)
	MarkProcessing(fileMD5, stage string, chunkID int) (*model.PipelineTask, error)
	MarkSuccess(fileMD5, stage string, chunkID int) error
	MarkRetry(fileMD5, stage string, chunkID int, lastError string) (int, error)
	MarkFailed(fileMD5, stage string, chunkID int, lastError string) error
	ListFailedByFile(fileMD5 string) ([]model.PipelineTask, error)
}

type pipelineTaskRepository struct {
	db *gorm.DB
}

func NewPipelineTaskRepository(db *gorm.DB) PipelineTaskRepository {
	return &pipelineTaskRepository{db: db}
}

func buildPipelineKey(fileMD5, stage string, chunkID int) string {
	return fmt.Sprintf("%s:%s:%d", fileMD5, stage, chunkID)
}

func (r *pipelineTaskRepository) GetByKey(fileMD5, stage string, chunkID int) (*model.PipelineTask, error) {
	var task model.PipelineTask
	err := r.db.Where("file_md5 = ? AND stage = ? AND chunk_id = ?", fileMD5, stage, chunkID).First(&task).Error
	if err != nil {
		return nil, err
	}
	return &task, nil
}

func (r *pipelineTaskRepository) MarkProcessing(fileMD5, stage string, chunkID int) (*model.PipelineTask, error) {
	task, err := r.GetByKey(fileMD5, stage, chunkID)
	if err != nil {
		if !errors.Is(err, gorm.ErrRecordNotFound) {
			return nil, err
		}
		task = &model.PipelineTask{
			FileMD5:        fileMD5,
			Stage:          stage,
			ChunkID:        chunkID,
			Status:         model.PipelineStatusProcessing,
			RetryCount:     0,
			IdempotencyKey: buildPipelineKey(fileMD5, stage, chunkID),
		}
		return task, r.db.Create(task).Error
	}
	task.Status = model.PipelineStatusProcessing
	return task, r.db.Save(task).Error
}

func (r *pipelineTaskRepository) MarkSuccess(fileMD5, stage string, chunkID int) error {
	task, err := r.MarkProcessing(fileMD5, stage, chunkID)
	if err != nil {
		return err
	}
	task.Status = model.PipelineStatusSuccess
	task.LastError = ""
	return r.db.Save(task).Error
}

func (r *pipelineTaskRepository) MarkRetry(fileMD5, stage string, chunkID int, lastError string) (int, error) {
	task, err := r.MarkProcessing(fileMD5, stage, chunkID)
	if err != nil {
		return 0, err
	}
	task.Status = model.PipelineStatusFailed
	task.RetryCount++
	task.LastError = lastError
	return task.RetryCount, r.db.Save(task).Error
}

func (r *pipelineTaskRepository) MarkFailed(fileMD5, stage string, chunkID int, lastError string) error {
	task, err := r.MarkProcessing(fileMD5, stage, chunkID)
	if err != nil {
		return err
	}
	task.Status = model.PipelineStatusFailed
	task.LastError = lastError
	return r.db.Save(task).Error
}

func (r *pipelineTaskRepository) ListFailedByFile(fileMD5 string) ([]model.PipelineTask, error) {
	var tasks []model.PipelineTask
	err := r.db.Where("file_md5 = ? AND status = ?", fileMD5, model.PipelineStatusFailed).Order("updated_at desc").Find(&tasks).Error
	return tasks, err
}

