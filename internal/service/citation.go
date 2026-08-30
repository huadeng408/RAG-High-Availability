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
		Excerpt:         document.TextContent,
	}
}
