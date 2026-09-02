package pipeline

import (
	"strings"
	"testing"

	"github.com/huadeng408/RAG-High-Availability/internal/model"
	"github.com/huadeng408/RAG-High-Availability/pkg/tasks"
)

func TestBuildStructuredVectorsPreservesVersionAndEvidenceLocation(t *testing.T) {
	task := tasks.FileProcessingTask{
		FileMD5:         "checksum-only",
		DocumentVersion: "version-20260831",
		UserID:          7,
		OrgTag:          "engineering",
	}
	parsed := model.ParsedDocument{
		DocumentVersion: task.DocumentVersion,
		EvidenceUnits: []model.EvidenceUnit{{
			ID:              "evidence-pdf-page-2",
			DocumentVersion: task.DocumentVersion,
			Modality:        "pdf",
			ElementType:     "ocr_text",
			Page:            2,
			BBox:            &model.BoundingBox{X0: 10, Y0: 20, X1: 30, Y1: 40},
			Image:           &model.ImageMetadata{AssetSHA256: "asset-sha", MIMEType: "image/png", Width: 640, Height: 480},
			Text:            "The retention period is seven years.",
		}},
	}
	chunks := []model.StructuredChunk{{
		ID:              "chunk-1",
		DocumentVersion: task.DocumentVersion,
		Text:            "The retention period is seven years.",
		Modality:        "pdf",
		Page:            2,
		EvidenceIDs:     []string{"evidence-pdf-page-2"},
	}}

	vectors, err := buildStructuredVectors(task, parsed, chunks, "fixture-embed")
	if err != nil {
		t.Fatal(err)
	}
	if len(vectors) != 1 {
		t.Fatalf("vectors = %d, want 1", len(vectors))
	}
	got := vectors[0]
	if got.DocumentVersion != task.DocumentVersion || got.Page != 2 || got.Modality != "pdf" {
		t.Fatalf("lost structured provenance: %#v", got)
	}
	if len(got.EvidenceIDs) != 1 || got.EvidenceIDs[0] != "evidence-pdf-page-2" {
		t.Fatalf("lost evidence IDs: %#v", got.EvidenceIDs)
	}
	if got.BBox == nil || got.BBox.Y1 != 40 {
		t.Fatalf("lost bbox: %#v", got.BBox)
	}
	if got.Image == nil || got.Image.AssetSHA256 != "asset-sha" || got.Image.Width != 640 {
		t.Fatalf("lost image metadata: %#v", got.Image)
	}
}

func TestBuildStructuredVectorsRejectsCrossVersionEvidence(t *testing.T) {
	parsed := model.ParsedDocument{
		DocumentVersion: "version-1",
		EvidenceUnits:   []model.EvidenceUnit{{ID: "evidence-other", DocumentVersion: "version-2"}},
	}
	chunks := []model.StructuredChunk{{
		ID:              "chunk-1",
		DocumentVersion: "version-1",
		Text:            "inconsistent evidence",
		EvidenceIDs:     []string{"evidence-other"},
	}}

	_, err := buildStructuredVectors(tasks.FileProcessingTask{DocumentVersion: "version-1"}, parsed, chunks, "fixture-embed")
	if err == nil || !strings.Contains(err.Error(), "belongs to document version") {
		t.Fatalf("expected cross-version evidence error, got %v", err)
	}
}
