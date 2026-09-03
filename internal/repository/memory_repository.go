// Package repository contains data-access code.
package repository

import (
	"context"
	"errors"
	"strings"
	"time"

	"github.com/huadeng408/RAG-High-Availability/internal/model"

	"gorm.io/gorm"
)

// MemoryRepository defines persistence operations for memory data.
type MemoryRepository interface {
	GetWorkingSnapshot(userID uint, conversationID string) (*model.WorkingMemorySnapshot, error)
	UpsertWorkingSnapshot(snapshot *model.WorkingMemorySnapshot) error
	ListProfileSlots(userID uint, limit int) ([]*model.UserProfileSlot, error)
	GetProfileSlot(userID uint, slotKey string) (*model.UserProfileSlot, error)
	UpsertProfileSlot(slot *model.UserProfileSlot) error
	CreateLongTermMemory(memory *model.LongTermMemory) error
	ClaimPendingLongTermMemories(ctx context.Context, limit int, lease time.Duration) ([]model.LongTermMemory, error)
	MarkLongTermMemoryIndexed(memoryID string, attempt int) error
	MarkLongTermMemoryIndexFailed(memoryID string, attempt int, lastError string, nextAttemptAt time.Time) error
}

// memoryRepository implements persistence operations for memory data.
type memoryRepository struct {
	db *gorm.DB
}

// NewMemoryRepository creates a memory repository.
func NewMemoryRepository(db *gorm.DB) MemoryRepository {
	return &memoryRepository{db: db}
}

// GetWorkingSnapshot returns working snapshot.
func (r *memoryRepository) GetWorkingSnapshot(userID uint, conversationID string) (*model.WorkingMemorySnapshot, error) {
	var snapshot model.WorkingMemorySnapshot
	err := r.db.Where("user_id = ? AND conversation_id = ?", userID, conversationID).First(&snapshot).Error
	if err != nil {
		return nil, err
	}
	return &snapshot, nil
}

// UpsertWorkingSnapshot handles upsert working snapshot.
func (r *memoryRepository) UpsertWorkingSnapshot(snapshot *model.WorkingMemorySnapshot) error {
	var existing model.WorkingMemorySnapshot
	err := r.db.Where("user_id = ? AND conversation_id = ?", snapshot.UserID, snapshot.ConversationID).First(&existing).Error
	if err != nil {
		if err == gorm.ErrRecordNotFound {
			return r.db.Create(snapshot).Error
		}
		return err
	}

	existing.Summary = snapshot.Summary
	existing.FactsJSON = snapshot.FactsJSON
	existing.EntitiesJSON = snapshot.EntitiesJSON
	existing.MessageCount = snapshot.MessageCount
	return r.db.Save(&existing).Error
}

// ListProfileSlots lists profile slots.
func (r *memoryRepository) ListProfileSlots(userID uint, limit int) ([]*model.UserProfileSlot, error) {
	if limit <= 0 {
		limit = 20
	}
	var slots []*model.UserProfileSlot
	err := r.db.Where("user_id = ?", userID).Order("updated_at desc").Limit(limit).Find(&slots).Error
	return slots, err
}

// GetProfileSlot returns profile slot.
func (r *memoryRepository) GetProfileSlot(userID uint, slotKey string) (*model.UserProfileSlot, error) {
	var slot model.UserProfileSlot
	err := r.db.Where("user_id = ? AND slot_key = ?", userID, slotKey).First(&slot).Error
	if err != nil {
		return nil, err
	}
	return &slot, nil
}

// UpsertProfileSlot handles upsert profile slot.
func (r *memoryRepository) UpsertProfileSlot(slot *model.UserProfileSlot) error {
	var existing model.UserProfileSlot
	err := r.db.Where("user_id = ? AND slot_key = ?", slot.UserID, slot.SlotKey).First(&existing).Error
	if err != nil {
		if err == gorm.ErrRecordNotFound {
			return r.db.Create(slot).Error
		}
		return err
	}

	existing.SlotValue = slot.SlotValue
	existing.Confidence = slot.Confidence
	existing.Source = slot.Source
	return r.db.Save(&existing).Error
}

// CreateLongTermMemory creates long term memory.
func (r *memoryRepository) CreateLongTermMemory(memory *model.LongTermMemory) error {
	return r.db.Create(memory).Error
}

// ClaimPendingLongTermMemories leases durable indexing work to one dispatcher instance.
func (r *memoryRepository) ClaimPendingLongTermMemories(ctx context.Context, limit int, lease time.Duration) ([]model.LongTermMemory, error) {
	if limit <= 0 {
		limit = 20
	}
	if lease <= 0 {
		lease = time.Minute
	}
	now := time.Now()
	expired := now.Add(-lease)
	availability := "(index_status = ? AND (index_next_attempt_at IS NULL OR index_next_attempt_at <= ?)) OR (index_status = ? AND index_claimed_at <= ?)"
	args := []any{model.MemoryIndexPending, now, model.MemoryIndexClaimed, expired}

	var candidates []model.LongTermMemory
	if err := r.db.WithContext(ctx).Where(availability, args...).Order("id asc").Limit(limit).Find(&candidates).Error; err != nil {
		return nil, err
	}

	claimed := make([]model.LongTermMemory, 0, len(candidates))
	for _, candidate := range candidates {
		result := r.db.WithContext(ctx).Model(&model.LongTermMemory{}).
			Where("id = ? AND ("+availability+")", append([]any{candidate.ID}, args...)...).
			Updates(map[string]any{
				"index_status":        model.MemoryIndexClaimed,
				"index_claimed_at":    now,
				"index_attempt_count": gorm.Expr("index_attempt_count + 1"),
			})
		if result.Error != nil {
			return claimed, result.Error
		}
		if result.RowsAffected == 1 {
			candidate.IndexStatus = model.MemoryIndexClaimed
			candidate.IndexClaimedAt = &now
			candidate.IndexAttemptCount++
			claimed = append(claimed, candidate)
		}
	}
	return claimed, nil
}

// MarkLongTermMemoryIndexed completes the active indexing lease.
func (r *memoryRepository) MarkLongTermMemoryIndexed(memoryID string, attempt int) error {
	now := time.Now()
	result := r.db.Model(&model.LongTermMemory{}).
		Where("memory_id = ? AND index_status = ? AND index_attempt_count = ?", memoryID, model.MemoryIndexClaimed, attempt).
		Updates(map[string]any{
			"index_status":          model.MemoryIndexIndexed,
			"index_claimed_at":      nil,
			"index_next_attempt_at": nil,
			"index_last_error":      "",
			"indexed_at":            &now,
		})
	if result.Error != nil {
		return result.Error
	}
	if result.RowsAffected != 1 {
		return errors.New("long-term memory indexing claim is stale")
	}
	return nil
}

// MarkLongTermMemoryIndexFailed releases the active lease for a delayed retry.
func (r *memoryRepository) MarkLongTermMemoryIndexFailed(memoryID string, attempt int, lastError string, nextAttemptAt time.Time) error {
	result := r.db.Model(&model.LongTermMemory{}).
		Where("memory_id = ? AND index_status = ? AND index_attempt_count = ?", memoryID, model.MemoryIndexClaimed, attempt).
		Updates(map[string]any{
			"index_status":          model.MemoryIndexPending,
			"index_claimed_at":      nil,
			"index_next_attempt_at": &nextAttemptAt,
			"index_last_error":      strings.TrimSpace(lastError),
		})
	if result.Error != nil {
		return result.Error
	}
	if result.RowsAffected != 1 {
		return errors.New("long-term memory indexing claim is stale")
	}
	return nil
}
