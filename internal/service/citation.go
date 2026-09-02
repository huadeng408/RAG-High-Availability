package service

import "github.com/huadeng408/RAG-High-Availability/internal/model"

// CitationFromDocument turns a retrieval hit into answer-ready source evidence.
func CitationFromDocument(document model.EsDocument) model.Citation {
	evidenceID := ""
	if len(document.EvidenceIDs) > 0 {
		evidenceID = document.EvidenceIDs[0]
	}
	return model.Citation{
		EvidenceID:      evidenceID,
		DocumentVersion: document.DocumentVersion,
		Modality:        document.Modality,
		Page:            document.Page,
		Slide:           document.Slide,
		Sheet:           document.Sheet,
		BBox:            document.BBox,
		Image:           document.Image,
		Excerpt:         document.TextContent,
	}
}

// CitationsFromDocument produces one page-level citation for every unique evidence reference on a retrieval hit.
func CitationsFromDocument(document model.EsDocument) []model.Citation {
	citations := make([]model.Citation, 0, len(document.EvidenceIDs))
	seen := make(map[string]struct{}, len(document.EvidenceIDs))
	for _, evidenceID := range document.EvidenceIDs {
		if evidenceID == "" {
			continue
		}
		if _, exists := seen[evidenceID]; exists {
			continue
		}
		seen[evidenceID] = struct{}{}
		citation := CitationFromDocument(document)
		citation.EvidenceID = evidenceID
		citations = append(citations, citation)
	}
	return citations
}
