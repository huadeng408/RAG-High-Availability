package model

import "testing"

func TestStructuredChunkRejectsEvidenceFromAnotherVersion(t *testing.T) {
	evidence := map[string]EvidenceUnit{
		"e-v2": {ID: "e-v2", DocumentVersion: "v2", Modality: "pdf", Page: 2},
	}
	chunk := StructuredChunk{
		ID:              "chunk-v1",
		DocumentVersion: "v1",
		EvidenceIDs:     []string{"e-v2"},
	}

	if err := chunk.Validate(evidence); err == nil {
		t.Fatal("expected cross-version evidence to be rejected")
	}
}

func TestStructuredChunkAcceptsEvidenceFromItsVersion(t *testing.T) {
	evidence := map[string]EvidenceUnit{
		"e-v1": {ID: "e-v1", DocumentVersion: "v1", Modality: "word"},
	}
	chunk := StructuredChunk{
		ID:              "chunk-v1",
		DocumentVersion: "v1",
		EvidenceIDs:     []string{"e-v1"},
	}

	if err := chunk.Validate(evidence); err != nil {
		t.Fatalf("expected same-version evidence to be accepted: %v", err)
	}
}

func TestCitationKeepsPageBoundingBoxAndSource(t *testing.T) {
	citation := NewCitation(EvidenceUnit{
		ID:              "pdf-01",
		DocumentVersion: "v-pdf",
		Modality:        "pdf",
		Page:            2,
		BBox:            &BoundingBox{X0: 1, Y0: 2, X1: 3, Y1: 4},
		Text:            "Payment terms are net 30 days.",
		AssetPath:       "parsed/v-pdf.json",
	})

	if citation.EvidenceID != "pdf-01" || citation.DocumentVersion != "v-pdf" {
		t.Fatalf("citation lost its identity: %#v", citation)
	}
	if citation.Page != 2 || citation.BBox == nil || citation.BBox.X1 != 3 {
		t.Fatalf("citation lost page-level location: %#v", citation)
	}
	if citation.Excerpt != "Payment terms are net 30 days." || citation.SourcePath != "parsed/v-pdf.json" {
		t.Fatalf("citation lost source provenance: %#v", citation)
	}
}

func TestCitationKeepsImagePixelMetadata(t *testing.T) {
	citation := NewCitation(EvidenceUnit{
		ID:              "image-01",
		DocumentVersion: "v-image",
		Modality:        "image",
		BBox:            &BoundingBox{X0: 12, Y0: 16, X1: 220, Y1: 48},
		Image: &ImageMetadata{
			AssetSHA256:           "asset-sha",
			MIMEType:              "image/png",
			Width:                 640,
			Height:                480,
			OrientationNormalized: true,
			OCRConfidence:         0.98,
		},
		Text:      "Gauge P-204 reads 10 bar.",
		AssetPath: "merged/image.png",
	})

	if citation.Image == nil || citation.Image.Width != 640 || citation.Image.Height != 480 {
		t.Fatalf("citation lost image dimensions: %#v", citation)
	}
	if citation.Image.AssetSHA256 != "asset-sha" || citation.Image.MIMEType != "image/png" {
		t.Fatalf("citation lost image identity: %#v", citation.Image)
	}
}
