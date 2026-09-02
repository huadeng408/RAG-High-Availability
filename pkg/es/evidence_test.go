package es

import (
	"testing"

	"github.com/huadeng408/RAG-High-Availability/internal/model"
)

func TestBuildEvidenceDocumentsUsesVersionQualifiedIDsAndFileMD5(t *testing.T) {
	docs := BuildEvidenceDocuments("file-md5", 7, "ops", false, []model.EvidenceUnit{{
		ID: "version-1:image:ocr:1", DocumentVersion: "version-1", Modality: "image",
		Page: 1, Text: "inspection", Image: &model.ImageMetadata{AssetSHA256: "asset"},
	}})
	if len(docs) != 1 || docs[0].EvidenceID != "version-1:image:ocr:1" {
		t.Fatalf("unexpected evidence documents: %#v", docs)
	}
	if docs[0].FileMD5 != "file-md5" || docs[0].OwnerID != "7" || docs[0].OrgTag != "ops" {
		t.Fatalf("missing evidence ownership metadata: %#v", docs[0])
	}
}
