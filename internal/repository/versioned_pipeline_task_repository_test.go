package repository

import (
	"testing"

	"github.com/glebarez/sqlite"
	"github.com/huadeng408/RAG-High-Availability/internal/model"
	"gorm.io/gorm"
)

func newSQLitePipelineTaskRepo(t *testing.T) PipelineTaskRepository {
	t.Helper()
	db, err := gorm.Open(sqlite.Open("file:"+t.Name()+"?mode=memory&cache=shared"), &gorm.Config{})
	if err != nil {
		t.Fatal(err)
	}
	if err := db.AutoMigrate(&model.PipelineTask{}); err != nil {
		t.Fatal(err)
	}
	return NewPipelineTaskRepository(db)
}

func TestGetOrStartUsesVersionStageAndWindowAsIdentity(t *testing.T) {
	repo := newSQLitePipelineTaskRepo(t)
	first, err := repo.GetOrStart("version-1", "embed", "0002")
	if err != nil {
		t.Fatal(err)
	}
	second, err := repo.GetOrStart("version-1", "embed", "0002")
	if err != nil {
		t.Fatal(err)
	}
	if first.ID != second.ID {
		t.Fatalf("duplicate task IDs: %d %d", first.ID, second.ID)
	}
}
