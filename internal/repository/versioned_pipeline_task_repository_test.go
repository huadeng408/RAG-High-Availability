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
	first, err := repo.GetOrStart("file-1", "version-1", "embed", "0002")
	if err != nil {
		t.Fatal(err)
	}
	second, err := repo.GetOrStart("file-1", "version-1", "embed", "0002")
	if err != nil {
		t.Fatal(err)
	}
	if first.ID != second.ID {
		t.Fatalf("duplicate task IDs: %d %d", first.ID, second.ID)
	}
}

func TestGetOrStartKeepsDifferentUploadVersionsDistinct(t *testing.T) {
	repo := newSQLitePipelineTaskRepo(t)
	legacyDB := repo.(*pipelineTaskRepository).db
	if err := legacyDB.Exec("CREATE UNIQUE INDEX uk_pipeline_file_stage_chunk ON pipeline_task(file_md5, stage, chunk_id)").Error; err != nil {
		t.Fatal(err)
	}

	first, err := repo.GetOrStart("11111111111111111111111111111111", "upload:11111111111111111111111111111111", "parse", "root")
	if err != nil {
		t.Fatal(err)
	}
	second, err := repo.GetOrStart("22222222222222222222222222222222", "upload:22222222222222222222222222222222", "parse", "root")
	if err != nil {
		t.Fatal(err)
	}
	if first.ID == second.ID {
		t.Fatalf("different upload versions share task ID %d", first.ID)
	}
}

func TestGetOrStartPersistsOriginalFileMD5ForVersionedTask(t *testing.T) {
	repo := newSQLitePipelineTaskRepo(t)

	task, err := repo.GetOrStart("abcdefabcdefabcdefabcdefabcdefab", "version-1", "chunk", "root")
	if err != nil {
		t.Fatal(err)
	}
	if task.FileMD5 != "abcdefabcdefabcdefabcdefabcdefab" {
		t.Fatalf("file MD5 = %q, want original upload MD5", task.FileMD5)
	}
}

func TestMarkRetryByKeyPersistsNextAttemptAndFinalError(t *testing.T) {
	repo := newSQLitePipelineTaskRepo(t)
	count, err := repo.MarkRetryByKey("file-1", "version-1", "parse", "root", "mineru unavailable")
	if err != nil {
		t.Fatal(err)
	}
	if count != 1 {
		t.Fatalf("retry count = %d, want 1", count)
	}
	task, err := repo.GetOrStart("file-1", "version-1", "parse", "root")
	if err != nil {
		t.Fatal(err)
	}
	if task.LastError != "mineru unavailable" || task.NextAttemptAt == nil {
		t.Fatalf("retry state not persisted: %#v", task)
	}
}
