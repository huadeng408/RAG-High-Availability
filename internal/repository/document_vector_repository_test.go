package repository

import (
	"testing"

	"github.com/glebarez/sqlite"
	"github.com/huadeng408/RAG-High-Availability/internal/model"
	"gorm.io/gorm"
)

func TestDocumentVectorRepositoryPreservesStructuredProvenance(t *testing.T) {
	db, err := gorm.Open(sqlite.Open("file:"+t.Name()+"?mode=memory&cache=shared"), &gorm.Config{})
	if err != nil {
		t.Fatal(err)
	}
	if err := db.AutoMigrate(&model.DocumentVector{}); err != nil {
		t.Fatal(err)
	}
	repo := NewDocumentVectorRepository(db)
	want := &model.DocumentVector{
		FileMD5: "checksum", DocumentVersion: "version-1", ChunkID: 0,
		TextContent: "retention", Modality: "pdf", Page: 2,
		EvidenceIDs: []string{"evidence-1"}, BBox: &model.BoundingBox{X0: 1, Y0: 2, X1: 3, Y1: 4},
		Image: &model.ImageMetadata{AssetSHA256: "asset-sha", MIMEType: "image/png", Width: 640, Height: 480},
	}
	if err := repo.BatchCreate([]*model.DocumentVector{want}); err != nil {
		t.Fatal(err)
	}
	got, err := repo.FindByDocumentVersion("version-1")
	if err != nil {
		t.Fatal(err)
	}
	if len(got) != 1 || got[0].DocumentVersion != "version-1" || got[0].Page != 2 {
		t.Fatalf("unexpected rows: %#v", got)
	}
	if len(got[0].EvidenceIDs) != 1 || got[0].EvidenceIDs[0] != "evidence-1" || got[0].BBox == nil || got[0].BBox.X1 != 3 {
		t.Fatalf("provenance did not round-trip: %#v", got[0])
	}
	if got[0].Image == nil || got[0].Image.AssetSHA256 != "asset-sha" || got[0].Image.Width != 640 {
		t.Fatalf("image metadata did not round-trip: %#v", got[0].Image)
	}
}
