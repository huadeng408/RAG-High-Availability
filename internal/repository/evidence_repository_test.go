package repository

import (
	"testing"

	"github.com/glebarez/sqlite"
	"github.com/huadeng408/RAG-High-Availability/internal/model"
	"gorm.io/gorm"
)

func TestEvidenceRepositoryPreservesPageBoundingBoxByVersion(t *testing.T) {
	db, err := gorm.Open(sqlite.Open("file:"+t.Name()+"?mode=memory&cache=shared"), &gorm.Config{})
	if err != nil {
		t.Fatal(err)
	}
	repo := NewEvidenceRepository(db)
	if err := db.AutoMigrate(&evidenceRecord{}); err != nil {
		t.Fatal(err)
	}
	evidence := model.EvidenceUnit{ID: "pdf-2", DocumentVersion: "v-pdf", Modality: "pdf", ElementType: "ocr_text", Page: 2, BBox: &model.BoundingBox{X0: 1, Y0: 2, X1: 3, Y1: 4}, Text: "Payment terms"}
	if err := repo.ReplaceForVersion("v-pdf", []model.EvidenceUnit{evidence}); err != nil {
		t.Fatal(err)
	}
	stored, err := repo.ListByVersion("v-pdf")
	if err != nil {
		t.Fatal(err)
	}
	if len(stored) != 1 || stored[0].Page != 2 || stored[0].BBox == nil || stored[0].BBox.X1 != 3 {
		t.Fatalf("lost page evidence: %#v", stored)
	}
}

func TestEvidenceRepositoryPreservesImageMetadata(t *testing.T) {
	db, err := gorm.Open(sqlite.Open("file:"+t.Name()+"?mode=memory&cache=shared"), &gorm.Config{})
	if err != nil {
		t.Fatal(err)
	}
	repo := NewEvidenceRepository(db)
	if err := db.AutoMigrate(&evidenceRecord{}); err != nil {
		t.Fatal(err)
	}
	evidence := model.EvidenceUnit{
		ID: "image-1", DocumentVersion: "v-image", Modality: "image", ElementType: "ocr_text",
		BBox: &model.BoundingBox{X0: 1, Y0: 2, X1: 30, Y1: 14}, Text: "valve A17",
		Image: &model.ImageMetadata{AssetSHA256: "asset-sha", MIMEType: "image/jpeg", Width: 80, Height: 40, OCRConfidence: 0.91},
	}
	if err := repo.ReplaceForVersion("v-image", []model.EvidenceUnit{evidence}); err != nil {
		t.Fatal(err)
	}
	stored, err := repo.ListByVersion("v-image")
	if err != nil {
		t.Fatal(err)
	}
	if len(stored) != 1 || stored[0].Image == nil || stored[0].Image.Width != 80 || stored[0].Image.OCRConfidence != 0.91 {
		t.Fatalf("lost image metadata: %#v", stored)
	}
}
