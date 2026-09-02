package pipeline

import (
	"fmt"

	"github.com/huadeng408/RAG-High-Availability/internal/model"
	"github.com/huadeng408/RAG-High-Availability/pkg/tasks"
)

// buildStructuredVectors converts parsed chunks into version-scoped DB rows.
// Evidence metadata is copied onto the chunk so search results can cite the source
// without another lookup on the hot path.
func buildStructuredVectors(task tasks.FileProcessingTask, parsed model.ParsedDocument, chunks []model.StructuredChunk, modelVersion string) ([]*model.DocumentVector, error) {
	if task.DocumentVersion == "" {
		return nil, fmt.Errorf("document version is required")
	}
	if parsed.DocumentVersion != "" && parsed.DocumentVersion != task.DocumentVersion {
		return nil, fmt.Errorf("parsed document belongs to document version %q, not %q", parsed.DocumentVersion, task.DocumentVersion)
	}
	evidenceByID := make(map[string]model.EvidenceUnit, len(parsed.EvidenceUnits))
	for _, evidence := range parsed.EvidenceUnits {
		if evidence.DocumentVersion != task.DocumentVersion {
			return nil, fmt.Errorf("evidence %q belongs to document version %q, not %q", evidence.ID, evidence.DocumentVersion, task.DocumentVersion)
		}
		evidenceByID[evidence.ID] = evidence
	}
	vectors := make([]*model.DocumentVector, 0, len(chunks))
	for index, chunk := range chunks {
		if chunk.DocumentVersion != task.DocumentVersion {
			return nil, fmt.Errorf("chunk %q belongs to document version %q, not %q", chunk.ID, chunk.DocumentVersion, task.DocumentVersion)
		}
		if err := chunk.Validate(evidenceByID); err != nil {
			return nil, err
		}
		row := &model.DocumentVector{
			FileMD5: task.FileMD5, DocumentVersion: task.DocumentVersion, ChunkID: index,
			TextContent: chunk.Text, ModelVersion: modelVersion, UserID: task.UserID,
			OrgTag: task.OrgTag, IsPublic: task.IsPublic, Modality: chunk.Modality,
			Page: chunk.Page, Slide: chunk.Slide, Sheet: chunk.Sheet,
			RowStart: chunk.RowStart, RowEnd: chunk.RowEnd, EvidenceIDs: append([]string(nil), chunk.EvidenceIDs...),
		}
		if len(chunk.EvidenceIDs) > 0 {
			first := evidenceByID[chunk.EvidenceIDs[0]]
			if row.Page == 0 {
				row.Page = first.Page
			}
			if row.Slide == 0 {
				row.Slide = first.Slide
			}
			if row.Sheet == "" {
				row.Sheet = first.Sheet
			}
			if row.RowStart == 0 {
				row.RowStart = first.RowStart
			}
			if row.RowEnd == 0 {
				row.RowEnd = first.RowEnd
			}
			row.BBox = first.BBox
			row.Image = first.Image
			if row.Modality == "" {
				row.Modality = first.Modality
			}
		}
		vectors = append(vectors, row)
	}
	return vectors, nil
}
