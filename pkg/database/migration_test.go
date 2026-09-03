package database

import (
	"strings"
	"sync"
	"testing"

	"github.com/glebarez/sqlite"
	"github.com/huadeng408/RAG-High-Availability/internal/model"
	"gorm.io/gorm"
	"gorm.io/gorm/schema"
)

func TestPipelineTaskSchemaRetainsLegacyNonHashIdempotencyCapacity(t *testing.T) {
	parsed, err := schema.Parse(&model.PipelineTask{}, &sync.Map{}, schema.NamingStrategy{})
	if err != nil {
		t.Fatal(err)
	}
	field := parsed.LookUpField("IdempotencyKey")
	if field == nil || field.TagSettings["TYPE"] != "varchar(255)" {
		t.Fatalf("idempotency ORM capacity = %v, want at least 255", field)
	}
}

func TestEnsureRuntimeSchemaAddsImageMetadataColumns(t *testing.T) {
	db, err := gorm.Open(sqlite.Open("file:"+t.Name()+"?mode=memory&cache=shared"), &gorm.Config{})
	if err != nil {
		t.Fatal(err)
	}
	if err := db.Exec("CREATE TABLE document_vectors (vector_id INTEGER PRIMARY KEY, model_version VARCHAR(64))").Error; err != nil {
		t.Fatal(err)
	}
	if err := db.Exec("CREATE TABLE evidence_units (evidence_id TEXT PRIMARY KEY)").Error; err != nil {
		t.Fatal(err)
	}
	previous := DB
	DB = db
	t.Cleanup(func() { DB = previous })

	if err := ensureImageMetadataColumns(); err != nil {
		t.Fatal(err)
	}
	for _, table := range []string{"document_vectors", "evidence_units"} {
		if !db.Migrator().HasColumn(table, "image_metadata") {
			t.Fatalf("%s.image_metadata was not added", table)
		}
	}
}

func TestEnsurePipelineTaskSchemaReplacesLegacyIdentityWithoutLosingRows(t *testing.T) {
	db, err := gorm.Open(sqlite.Open("file:"+t.Name()+"?mode=memory&cache=shared"), &gorm.Config{})
	if err != nil {
		t.Fatal(err)
	}
	if err := db.Exec(`CREATE TABLE pipeline_task (
		id INTEGER PRIMARY KEY,
		file_md5 VARCHAR(32) NOT NULL,
		stage VARCHAR(20) NOT NULL,
		chunk_id INTEGER NOT NULL DEFAULT -1,
		status VARCHAR(20) NOT NULL,
		retry_count INTEGER NOT NULL DEFAULT 0,
		idempotency_key VARCHAR(96) NOT NULL
	)`).Error; err != nil {
		t.Fatal(err)
	}
	if err := db.Exec("CREATE UNIQUE INDEX uk_pipeline_file_stage_chunk ON pipeline_task(file_md5, stage, chunk_id)").Error; err != nil {
		t.Fatal(err)
	}
	fileMD5 := "0123456789abcdef0123456789abcdef"
	legacyKey := strings.Repeat("legacy-key-", 20)
	if err := db.Exec(
		"INSERT INTO pipeline_task (id, file_md5, stage, chunk_id, status, retry_count, idempotency_key) VALUES (1, ?, 'embed', -1, 'FAILED', 3, ?)",
		fileMD5, legacyKey,
	).Error; err != nil {
		t.Fatal(err)
	}
	previous := DB
	DB = db
	t.Cleanup(func() { DB = previous })

	if err := ensurePipelineTaskSchema(); err != nil {
		t.Fatal(err)
	}
	if !db.Migrator().HasColumn("pipeline_task", "document_version") || !db.Migrator().HasColumn("pipeline_task", "window_id") {
		t.Fatal("versioned pipeline identity columns were not added")
	}
	if db.Migrator().HasIndex("pipeline_task", "uk_pipeline_file_stage_chunk") {
		t.Fatal("legacy file/stage/chunk unique index still exists")
	}
	if !db.Migrator().HasIndex("pipeline_task", "idx_document_version_stage_window") {
		t.Fatal("version/stage/window unique index was not created")
	}
	var migrated struct {
		DocumentVersion string
		WindowID        string
	}
	if err := db.Table("pipeline_task").Select("document_version", "window_id").Where("id = 1").Scan(&migrated).Error; err != nil {
		t.Fatal(err)
	}
	if migrated.DocumentVersion != "upload:"+fileMD5 || migrated.WindowID != "root" {
		t.Fatalf("legacy identity backfill = %#v", migrated)
	}
	var migratedKey string
	if err := db.Table("pipeline_task").Select("idempotency_key").Where("id = 1").Scan(&migratedKey).Error; err != nil {
		t.Fatal(err)
	}
	if migratedKey != legacyKey {
		t.Fatalf("legacy idempotency key changed during migration: got %q", migratedKey)
	}
	var attemptCount int
	if err := db.Table("pipeline_task").Select("attempt_count").Where("id = 1").Scan(&attemptCount).Error; err != nil {
		t.Fatal(err)
	}
	if attemptCount != 3 {
		t.Fatalf("failed legacy attempt count = %d, want retry_count 3", attemptCount)
	}
	for _, column := range []string{"attempt_count", "publication_status", "publication_attempt_count", "publication_claimed_at", "published_at", "publication_last_error"} {
		if !db.Migrator().HasColumn("pipeline_task", column) {
			t.Fatalf("pipeline_task.%s was not added", column)
		}
	}
	if err := db.Exec(
		"INSERT INTO pipeline_task (id, file_md5, stage, chunk_id, status, idempotency_key, document_version, window_id) VALUES (2, ?, 'embed', -1, 'PENDING', 'versioned', 'version-sha', 'window-2')",
		fileMD5,
	).Error; err != nil {
		t.Fatalf("versioned window still conflicts with legacy identity: %v", err)
	}
}
