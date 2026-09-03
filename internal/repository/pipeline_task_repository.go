// Package repository contains data-access code.
package repository

import (
	"context"
	"crypto/md5"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"strings"
	"time"

	"github.com/huadeng408/RAG-High-Availability/internal/model"
	"github.com/huadeng408/RAG-High-Availability/pkg/tasks"
	"gorm.io/gorm"
	"gorm.io/gorm/clause"
)

// EnqueueInitialTask durably records the complete initial payload before Kafka publication.
func (r *pipelineTaskRepository) EnqueueInitialTask(task tasks.FileProcessingTask) error {
	return enqueueInitialTask(r.db, task)
}

// CompleteUploadAndEnqueueInitialTask commits upload completion and initial pipeline acceptance atomically.
func (r *pipelineTaskRepository) CompleteUploadAndEnqueueInitialTask(uploadRecordID uint, task tasks.FileProcessingTask) error {
	return r.db.Transaction(func(tx *gorm.DB) error {
		result := tx.Model(&model.FileUpload{}).Where("id = ?", uploadRecordID).Update("status", 1)
		if result.Error != nil {
			return result.Error
		}
		if result.RowsAffected != 1 {
			return errors.New("upload record not found")
		}
		return enqueueInitialTask(tx, task)
	})
}

func enqueueInitialTask(db *gorm.DB, task tasks.FileProcessingTask) error {
	documentVersion := strings.TrimSpace(task.DocumentVersion)
	if documentVersion == "" {
		documentVersion = "upload:" + task.FileMD5
	}
	task.DocumentVersion = documentVersion
	payload, err := json.Marshal(task)
	if err != nil {
		return err
	}
	txRepo := &pipelineTaskRepository{db: db}
	row, err := txRepo.GetOrStart(task.FileMD5, documentVersion, string(task.Stage), "root")
	if err != nil {
		return err
	}
	updates := map[string]any{
		"task_payload":           string(payload),
		"last_trace_id":          strings.TrimSpace(task.TraceID),
		"publication_claimed_at": nil,
		"publication_last_error": "",
	}
	if row.PublicationStatus != model.PipelinePublicationPublished {
		updates["publication_status"] = model.PipelinePublicationPending
	}
	return db.Model(row).Updates(updates).Error
}

// ClaimPendingInitialTasks leases durable initial publications to one dispatcher.
func (r *pipelineTaskRepository) ClaimPendingInitialTasks(ctx context.Context, limit int, lease time.Duration) ([]model.PipelineTask, error) {
	if limit <= 0 {
		limit = 20
	}
	if lease <= 0 {
		lease = time.Minute
	}
	now := time.Now()
	expired := now.Add(-lease)
	query := r.db.WithContext(ctx).Where(
		"task_payload <> '' AND (publication_status = ? OR (publication_status = ? AND publication_claimed_at <= ?))",
		model.PipelinePublicationPending,
		model.PipelinePublicationClaimed,
		expired,
	).Order("id asc").Limit(limit)
	var candidates []model.PipelineTask
	if err := query.Find(&candidates).Error; err != nil {
		return nil, err
	}

	claimed := make([]model.PipelineTask, 0, len(candidates))
	for _, candidate := range candidates {
		result := r.db.WithContext(ctx).Model(&model.PipelineTask{}).
			Where(
				"id = ? AND (publication_status = ? OR (publication_status = ? AND publication_claimed_at <= ?))",
				candidate.ID,
				model.PipelinePublicationPending,
				model.PipelinePublicationClaimed,
				expired,
			).
			Updates(map[string]any{
				"publication_status":        model.PipelinePublicationClaimed,
				"publication_claimed_at":    now,
				"publication_attempt_count": gorm.Expr("publication_attempt_count + 1"),
			})
		if result.Error != nil {
			return claimed, result.Error
		}
		if result.RowsAffected == 1 {
			candidate.PublicationStatus = model.PipelinePublicationClaimed
			candidate.PublicationClaimedAt = &now
			candidate.PublicationAttemptCount++
			claimed = append(claimed, candidate)
		}
	}
	return claimed, nil
}

// MarkInitialTaskPublished records Kafka acknowledgement for the active claim.
func (r *pipelineTaskRepository) MarkInitialTaskPublished(taskID uint, publicationAttempt int) error {
	now := time.Now()
	result := r.db.Model(&model.PipelineTask{}).
		Where("id = ? AND publication_status = ? AND publication_attempt_count = ?", taskID, model.PipelinePublicationClaimed, publicationAttempt).
		Updates(map[string]any{
			"publication_status":     model.PipelinePublicationPublished,
			"publication_claimed_at": nil,
			"publication_last_error": "",
			"published_at":           &now,
		})
	if result.Error != nil {
		return result.Error
	}
	if result.RowsAffected != 1 {
		return errors.New("initial task publication claim is stale")
	}
	return nil
}

// MarkInitialTaskPublicationFailed releases a failed claim for automatic retry.
func (r *pipelineTaskRepository) MarkInitialTaskPublicationFailed(taskID uint, publicationAttempt int, lastError string) error {
	result := r.db.Model(&model.PipelineTask{}).
		Where("id = ? AND publication_status = ? AND publication_attempt_count = ?", taskID, model.PipelinePublicationClaimed, publicationAttempt).
		Updates(map[string]any{
			"publication_status":     model.PipelinePublicationPending,
			"publication_claimed_at": nil,
			"publication_last_error": strings.TrimSpace(lastError),
		})
	if result.Error != nil {
		return result.Error
	}
	if result.RowsAffected != 1 {
		return errors.New("initial task publication claim is stale")
	}
	return nil
}

// PipelineTaskRepository defines persistence operations for pipeline task data.
type PipelineTaskRepository interface {
	EnqueueInitialTask(task tasks.FileProcessingTask) error
	CompleteUploadAndEnqueueInitialTask(uploadRecordID uint, task tasks.FileProcessingTask) error
	ClaimPendingInitialTasks(ctx context.Context, limit int, lease time.Duration) ([]model.PipelineTask, error)
	MarkInitialTaskPublished(taskID uint, publicationAttempt int) error
	MarkInitialTaskPublicationFailed(taskID uint, publicationAttempt int, lastError string) error
	GetOrStart(fileMD5, documentVersion, stage, windowID string) (*model.PipelineTask, error)
	MarkProcessingByKey(fileMD5, documentVersion, stage, windowID string) (*model.PipelineTask, error)
	MarkSuccessByKey(fileMD5, documentVersion, stage, windowID string) error
	MarkRetryByKey(fileMD5, documentVersion, stage, windowID, lastError string) (int, error)
	MarkFailedByKey(fileMD5, documentVersion, stage, windowID, lastError string) error
	MarkDeadLetterByKey(fileMD5, documentVersion, stage, windowID, lastError, payload, messageID string) error
	GetDeadLetterByKey(fileMD5, documentVersion, stage, windowID string) (payload, messageID string, err error)
	ResetForReplayByKey(fileMD5, documentVersion, stage, windowID string) error
	RecordAttemptMetadata(fileMD5, documentVersion, stage, windowID, traceID, errorClass string) error
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
	return buildVersionedPipelineKey(fileMD5, stage, fmt.Sprintf("%d", chunkID))
}

func buildVersionedPipelineKey(documentVersion, stage, windowID string) string {
	digest := sha256.Sum256([]byte(documentVersion + "\x00" + stage + "\x00" + windowID))
	return hex.EncodeToString(digest[:])
}

// GetOrStart atomically creates or loads one versioned pipeline task.
func (r *pipelineTaskRepository) GetOrStart(fileMD5, documentVersion, stage, windowID string) (*model.PipelineTask, error) {
	fileMD5 = normalizePipelineFileMD5(fileMD5, documentVersion)
	task := &model.PipelineTask{
		FileMD5:         fileMD5,
		DocumentVersion: documentVersion,
		Stage:           stage,
		WindowID:        windowID,
		Status:          model.PipelineStatusPending,
		ChunkID:         -1,
		IdempotencyKey:  buildVersionedPipelineKey(documentVersion, stage, windowID),
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

func (r *pipelineTaskRepository) RecordAttemptMetadata(fileMD5, documentVersion, stage, windowID, traceID, errorClass string) error {
	task, err := r.GetOrStart(fileMD5, documentVersion, stage, windowID)
	if err != nil {
		return err
	}
	updates := map[string]any{"last_trace_id": strings.TrimSpace(traceID)}
	if strings.TrimSpace(errorClass) != "" {
		updates["error_class"] = strings.TrimSpace(errorClass)
	}
	return r.db.Model(task).Updates(updates).Error
}

// pipelineTaskFileMD5 fills the legacy file identity column for versioned tasks.
// Upload versions already carry the real MD5; other version formats get a stable surrogate.
func normalizePipelineFileMD5(fileMD5, documentVersion string) string {
	if value := strings.TrimSpace(fileMD5); value != "" {
		return value
	}
	if strings.HasPrefix(documentVersion, "upload:") {
		candidate := strings.TrimPrefix(documentVersion, "upload:")
		if len(candidate) == 32 {
			return candidate
		}
	}
	digest := md5.Sum([]byte(documentVersion))
	return hex.EncodeToString(digest[:])
}

func (r *pipelineTaskRepository) MarkProcessingByKey(fileMD5, documentVersion, stage, windowID string) (*model.PipelineTask, error) {
	task, err := r.GetOrStart(fileMD5, documentVersion, stage, windowID)
	if err != nil {
		return nil, err
	}
	if err := r.db.Model(task).Updates(map[string]any{
		"status":        model.PipelineStatusProcessing,
		"attempt_count": gorm.Expr("attempt_count + 1"),
	}).Error; err != nil {
		return nil, err
	}
	return r.GetOrStart(fileMD5, documentVersion, stage, windowID)
}

func (r *pipelineTaskRepository) MarkSuccessByKey(fileMD5, documentVersion, stage, windowID string) error {
	task, err := r.GetOrStart(fileMD5, documentVersion, stage, windowID)
	if err != nil {
		return err
	}
	task.Status = model.PipelineStatusSuccess
	task.LastError = ""
	task.NextAttemptAt = nil
	return r.db.Save(task).Error
}

func (r *pipelineTaskRepository) MarkRetryByKey(fileMD5, documentVersion, stage, windowID, lastError string) (int, error) {
	task, err := r.GetOrStart(fileMD5, documentVersion, stage, windowID)
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

func (r *pipelineTaskRepository) MarkFailedByKey(fileMD5, documentVersion, stage, windowID, lastError string) error {
	task, err := r.GetOrStart(fileMD5, documentVersion, stage, windowID)
	if err != nil {
		return err
	}
	task.Status = model.PipelineStatusFailed
	task.LastError = lastError
	return r.db.Save(task).Error
}

func (r *pipelineTaskRepository) MarkDeadLetterByKey(
	fileMD5, documentVersion, stage, windowID, lastError, payload, messageID string,
) error {
	if strings.TrimSpace(payload) == "" || strings.TrimSpace(messageID) == "" {
		return errors.New("dead-letter payload and message ID are required")
	}
	task, err := r.GetOrStart(fileMD5, documentVersion, stage, windowID)
	if err != nil {
		return err
	}
	now := time.Now()
	task.Status = model.PipelineStatusFailed
	task.LastError = lastError
	task.NextAttemptAt = nil
	task.DLQMessageID = messageID
	task.DLQPayload = payload
	task.DeadLetteredAt = &now
	return r.db.Save(task).Error
}

// GetDeadLetterByKey returns the durable DLQ envelope for an exact task identity.
func (r *pipelineTaskRepository) GetDeadLetterByKey(fileMD5, documentVersion, stage, windowID string) (string, string, error) {
	var task model.PipelineTask
	err := r.db.Where(
		"file_md5 = ? AND document_version = ? AND stage = ? AND window_id = ? AND status = ? AND dlq_message_id <> ''",
		fileMD5, documentVersion, stage, windowID, model.PipelineStatusFailed,
	).First(&task).Error
	if errors.Is(err, gorm.ErrRecordNotFound) {
		return "", "", nil
	}
	if err != nil {
		return "", "", err
	}
	return task.DLQPayload, task.DLQMessageID, nil
}

func (r *pipelineTaskRepository) ResetForReplayByKey(fileMD5, documentVersion, stage, windowID string) error {
	now := time.Now()
	result := r.db.Model(&model.PipelineTask{}).
		Where(
			"file_md5 = ? AND document_version = ? AND stage = ? AND window_id = ? AND status = ? AND dlq_message_id <> ''",
			fileMD5,
			documentVersion,
			stage,
			windowID,
			model.PipelineStatusFailed,
		).
		Updates(map[string]any{
			"status":           model.PipelineStatusPending,
			"last_error":       "",
			"next_attempt_at":  nil,
			"replay_count":     gorm.Expr("replay_count + 1"),
			"last_replayed_at": &now,
		})
	if result.Error != nil {
		return result.Error
	}
	if result.RowsAffected != 1 {
		return errors.New("dead-letter task not found or already replayed")
	}
	return nil
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
			AttemptCount:    1,
			IdempotencyKey:  buildPipelineKey(fileMD5, stage, chunkID),
		}
		return task, r.db.Create(task).Error
	}
	if err := r.db.Model(task).Updates(map[string]any{
		"status":        model.PipelineStatusProcessing,
		"attempt_count": gorm.Expr("attempt_count + 1"),
	}).Error; err != nil {
		return nil, err
	}
	return r.GetByKey(fileMD5, stage, chunkID)
}

// MarkSuccess handles mark success.
func (r *pipelineTaskRepository) MarkSuccess(fileMD5, stage string, chunkID int) error {
	task, err := r.GetByKey(fileMD5, stage, chunkID)
	if err != nil {
		return err
	}
	task.Status = model.PipelineStatusSuccess
	task.LastError = ""
	return r.db.Save(task).Error
}

// MarkRetry handles mark retry.
func (r *pipelineTaskRepository) MarkRetry(fileMD5, stage string, chunkID int, lastError string) (int, error) {
	task, err := r.GetByKey(fileMD5, stage, chunkID)
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
	task, err := r.GetByKey(fileMD5, stage, chunkID)
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
