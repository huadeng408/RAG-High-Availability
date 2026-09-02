package database

import (
	"testing"

	"github.com/glebarez/sqlite"
	"gorm.io/gorm"
)

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
