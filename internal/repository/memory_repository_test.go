package repository

import (
	"context"
	"path/filepath"
	"testing"
	"time"

	"github.com/glebarez/sqlite"
	"github.com/huadeng408/RAG-High-Availability/internal/model"
	"gorm.io/gorm"
)

func openMemoryOutbox(t *testing.T) (*memoryRepository, *gorm.DB) {
	t.Helper()
	databasePath := filepath.Join(t.TempDir(), "memory-outbox.db")
	db, err := gorm.Open(sqlite.Open(databasePath), &gorm.Config{})
	if err != nil {
		t.Fatal(err)
	}
	if err := db.AutoMigrate(&model.LongTermMemory{}); err != nil {
		t.Fatal(err)
	}
	sqlDB, err := db.DB()
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() {
		if err := sqlDB.Close(); err != nil {
			t.Errorf("close memory outbox database: %v", err)
		}
	})
	return &memoryRepository{db: db}, db
}

func TestMemoryIndexOutboxPersistsFailureAndCompletesRetry(t *testing.T) {
	repo, db := openMemoryOutbox(t)
	entry := &model.LongTermMemory{
		MemoryID: "memory-1", UserID: 7, ConversationID: "conversation-7",
		MemoryType: "project", Content: "durable marker", Summary: "durable marker",
		Importance: 0.9, IndexStatus: model.MemoryIndexPending,
	}
	if err := repo.CreateLongTermMemory(entry); err != nil {
		t.Fatal(err)
	}

	claimed, err := repo.ClaimPendingLongTermMemories(context.Background(), 10, time.Minute)
	if err != nil || len(claimed) != 1 {
		t.Fatalf("first claim = %#v, err=%v", claimed, err)
	}
	if claimed[0].IndexStatus != model.MemoryIndexClaimed || claimed[0].IndexAttemptCount != 1 {
		t.Fatalf("first claim state = %#v", claimed[0])
	}
	nextAttempt := time.Now().Add(time.Hour)
	if err := repo.MarkLongTermMemoryIndexFailed(entry.MemoryID, 1, "embedding unavailable", nextAttempt); err != nil {
		t.Fatal(err)
	}
	claimed, err = repo.ClaimPendingLongTermMemories(context.Background(), 10, time.Minute)
	if err != nil || len(claimed) != 0 {
		t.Fatalf("future retry was claimed: %#v, err=%v", claimed, err)
	}

	if err := db.Model(&model.LongTermMemory{}).Where("memory_id = ?", entry.MemoryID).
		Update("index_next_attempt_at", time.Now().Add(-time.Second)).Error; err != nil {
		t.Fatal(err)
	}
	claimed, err = repo.ClaimPendingLongTermMemories(context.Background(), 10, time.Minute)
	if err != nil || len(claimed) != 1 || claimed[0].IndexAttemptCount != 2 {
		t.Fatalf("retry claim = %#v, err=%v", claimed, err)
	}
	if err := repo.MarkLongTermMemoryIndexed(entry.MemoryID, 2); err != nil {
		t.Fatal(err)
	}

	var stored model.LongTermMemory
	if err := db.Where("memory_id = ?", entry.MemoryID).First(&stored).Error; err != nil {
		t.Fatal(err)
	}
	if stored.IndexStatus != model.MemoryIndexIndexed || stored.IndexedAt == nil {
		t.Fatalf("completed state = %#v", stored)
	}
	if stored.IndexLastError != "" || stored.IndexNextAttemptAt != nil || stored.IndexClaimedAt != nil {
		t.Fatalf("completed state retained retry metadata: %#v", stored)
	}
	claimed, err = repo.ClaimPendingLongTermMemories(context.Background(), 10, time.Minute)
	if err != nil || len(claimed) != 0 {
		t.Fatalf("indexed memory was reclaimed: %#v, err=%v", claimed, err)
	}
}

func TestMemoryIndexOutboxReclaimsExpiredLeaseOnly(t *testing.T) {
	repo, db := openMemoryOutbox(t)
	entry := &model.LongTermMemory{
		MemoryID: "memory-lease", UserID: 8, ConversationID: "conversation-8",
		MemoryType: "project", Content: "lease marker", Summary: "lease marker",
		Importance: 0.8, IndexStatus: model.MemoryIndexPending,
	}
	if err := repo.CreateLongTermMemory(entry); err != nil {
		t.Fatal(err)
	}
	claimed, err := repo.ClaimPendingLongTermMemories(context.Background(), 1, time.Minute)
	if err != nil || len(claimed) != 1 {
		t.Fatalf("claim = %#v, err=%v", claimed, err)
	}
	claimed, err = repo.ClaimPendingLongTermMemories(context.Background(), 1, time.Minute)
	if err != nil || len(claimed) != 0 {
		t.Fatalf("active lease was reclaimed: %#v, err=%v", claimed, err)
	}
	if err := db.Model(&model.LongTermMemory{}).Where("memory_id = ?", entry.MemoryID).
		Update("index_claimed_at", time.Now().Add(-2*time.Minute)).Error; err != nil {
		t.Fatal(err)
	}
	claimed, err = repo.ClaimPendingLongTermMemories(context.Background(), 1, time.Minute)
	if err != nil || len(claimed) != 1 || claimed[0].IndexAttemptCount != 2 {
		t.Fatalf("expired lease claim = %#v, err=%v", claimed, err)
	}
}
