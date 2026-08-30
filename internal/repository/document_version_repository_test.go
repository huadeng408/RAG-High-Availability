package repository

import (
	"crypto/sha256"
	"fmt"
	"testing"

	"github.com/glebarez/sqlite"
	"github.com/huadeng408/RAG-High-Availability/internal/model"
	"gorm.io/gorm"
)

func TestCreateForUploadUsesImmutableContentHashVersion(t *testing.T) {
	db, err := gorm.Open(sqlite.Open("file:"+t.Name()+"?mode=memory&cache=shared"), &gorm.Config{})
	if err != nil {
		t.Fatal(err)
	}
	if err := db.AutoMigrate(&model.DocumentVersion{}); err != nil {
		t.Fatal(err)
	}
	repo := NewDocumentVersionRepository(db)
	source := model.DocumentSource{SourceID: "source-1", FileName: "receipt.pdf"}
	contents := []byte("invoice-content")

	first, err := repo.CreateForUpload(source, contents, "mineru+ocr", "1")
	if err != nil {
		t.Fatal(err)
	}
	second, err := repo.CreateForUpload(source, contents, "mineru+ocr", "1")
	if err != nil {
		t.Fatal(err)
	}
	wantHash := fmt.Sprintf("%x", sha256.Sum256(contents))
	if first.ContentSHA256 != wantHash || first.DocumentVersionID != second.DocumentVersionID {
		t.Fatalf("expected stable hash-backed version, got %#v and %#v", first, second)
	}
}
