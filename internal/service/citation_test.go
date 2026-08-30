package service

import (
	"testing"

	"github.com/huadeng408/RAG-High-Availability/internal/model"
)

func TestCitationFromDocumentKeepsPageAndBoundingBox(t *testing.T) {
	hit := model.EsDocument{
		DocumentVersion: "v1",
		Modality:        "pdf",
		Page:            3,
		EvidenceIDs:     []string{"e3"},
		BBox:            &model.BoundingBox{X0: 1, Y0: 2, X1: 3, Y1: 4},
		TextContent:     "Payment terms",
	}
	citation := CitationFromDocument(hit)
	if citation.EvidenceID != "e3" || citation.Page != 3 || citation.BBox == nil || citation.BBox.X1 != 3 {
		t.Fatalf("lost evidence location: %#v", citation)
	}
	if citation.DocumentVersion != "v1" || citation.Excerpt != "Payment terms" {
		t.Fatalf("lost evidence provenance: %#v", citation)
	}
}

func TestCitationsFromDocumentKeepsEachEvidenceID(t *testing.T) {
	hit := model.EsDocument{
		DocumentVersion: "v1",
		Modality:        "pdf",
		Page:            3,
		EvidenceIDs:     []string{"e3", "e4", "e3"},
		BBox:            &model.BoundingBox{X0: 1, Y0: 2, X1: 3, Y1: 4},
		TextContent:     "Payment terms",
	}

	citations := CitationsFromDocument(hit)
	if len(citations) != 2 {
		t.Fatalf("expected two unique citations, got %#v", citations)
	}
	if citations[0].EvidenceID != "e3" || citations[1].EvidenceID != "e4" || citations[1].Page != 3 || citations[1].BBox == nil {
		t.Fatalf("lost evidence locations: %#v", citations)
	}
}
