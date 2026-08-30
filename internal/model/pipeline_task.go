// Package model contains persistent models and DTOs.
package model

import "time"

const (
	PipelineStatusPending    = "PENDING"
	PipelineStatusProcessing = "PROCESSING"
	PipelineStatusSuccess    = "SUCCESS"
	PipelineStatusFailed     = "FAILED"
)

// PipelineTask tracks per-stage processing status for replay and observability.
type PipelineTask struct {
	ID              uint       `gorm:"primaryKey;autoIncrement" json:"id"`
	FileMD5         string     `gorm:"type:varchar(32);not null" json:"fileMd5"`
	DocumentVersion string     `gorm:"type:varchar(96);not null;uniqueIndex:idx_document_version_stage_window,priority:1" json:"documentVersion"`
	Stage           string     `gorm:"type:varchar(20);not null;uniqueIndex:idx_document_version_stage_window,priority:2" json:"stage"`
	WindowID        string     `gorm:"type:varchar(64);not null;uniqueIndex:idx_document_version_stage_window,priority:3" json:"windowId"`
	ChunkID         int        `gorm:"not null;default:-1" json:"chunkId"`
	Status          string     `gorm:"type:varchar(20);not null;index" json:"status"`
	RetryCount      int        `gorm:"not null;default:0" json:"retryCount"`
	LastError       string     `gorm:"type:text" json:"lastError"`
	NextAttemptAt   *time.Time `json:"nextAttemptAt,omitempty"`
	IdempotencyKey  string     `gorm:"type:varchar(160);not null;uniqueIndex" json:"idempotencyKey"`
	UpdatedAt       time.Time  `gorm:"autoUpdateTime" json:"updatedAt"`
	CreatedAt       time.Time  `gorm:"autoCreateTime" json:"createdAt"`
}

// TableName handles table name.
func (PipelineTask) TableName() string {
	return "pipeline_task"
}
