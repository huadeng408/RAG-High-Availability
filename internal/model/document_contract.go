package model

import (
	"fmt"
	"strings"
	"time"
)

// DocumentSource identifies the immutable upload a version is derived from.
type DocumentSource struct {
	SourceID          string `json:"sourceId"`
	FileName          string `json:"fileName"`
	MediaType         string `json:"mediaType"`
	OwnerID           string `json:"ownerId"`
	Organization      string `json:"organization"`
	IsPublic          bool   `json:"isPublic"`
	OriginalObjectKey string `json:"originalObjectKey"`
	FileMD5           string `json:"fileMd5"`
}

// DocumentVersion records the parser receipt for one immutable source version.
type DocumentVersion struct {
	DocumentVersionID string    `json:"documentVersion"`
	SourceID          string    `json:"sourceId"`
	ContentSHA256     string    `json:"contentSha256"`
	ParserName        string    `json:"parserName"`
	ParserVersion     string    `json:"parserVersion"`
	CreatedAt         time.Time `json:"createdAt"`
}

// BoundingBox locates visual evidence in source-page coordinates.
type BoundingBox struct {
	X0 float64 `json:"x0"`
	Y0 float64 `json:"y0"`
	X1 float64 `json:"x1"`
	Y1 float64 `json:"y1"`
}

// EvidenceUnit is the smallest citeable source element.
type EvidenceUnit struct {
	ID              string       `json:"evidenceId"`
	DocumentVersion string       `json:"documentVersion"`
	Modality        string       `json:"modality"`
	ElementType     string       `json:"elementType"`
	Page            int          `json:"page,omitempty"`
	Slide           int          `json:"slide,omitempty"`
	Sheet           string       `json:"sheet,omitempty"`
	RowStart        int          `json:"rowStart,omitempty"`
	RowEnd          int          `json:"rowEnd,omitempty"`
	HeadingPath     []string     `json:"headingPath,omitempty"`
	Header          []string     `json:"header,omitempty"`
	BBox            *BoundingBox `json:"bbox,omitempty"`
	Text            string       `json:"text"`
	ParserName      string       `json:"parserName"`
	ParserVersion   string       `json:"parserVersion"`
	AssetPath       string       `json:"assetPath"`
}

// StructuredChunk is the retrieval unit built from one or more evidence units.
type StructuredChunk struct {
	ID              string   `json:"id"`
	DocumentVersion string   `json:"documentVersion"`
	Text            string   `json:"text"`
	Modality        string   `json:"modality"`
	HeadingPath     []string `json:"headingPath,omitempty"`
	Page            int      `json:"page,omitempty"`
	Slide           int      `json:"slide,omitempty"`
	Sheet           string   `json:"sheet,omitempty"`
	RowStart        int      `json:"rowStart,omitempty"`
	RowEnd          int      `json:"rowEnd,omitempty"`
	EvidenceIDs     []string `json:"evidenceIds"`
}

// ParserReceipt records the parser engine that produced a structured artifact.
type ParserReceipt struct {
	Engine       string `json:"engine"`
	Version      string `json:"version,omitempty"`
	OCRPerformed bool   `json:"ocrPerformed,omitempty"`
}

// ParsedDocument is the versioned artifact exchanged between Go and Python.
type ParsedDocument struct {
	SourceID        string            `json:"sourceId,omitempty"`
	FileName        string            `json:"fileName,omitempty"`
	DocumentVersion string            `json:"documentVersion"`
	Modality        string            `json:"modality"`
	ParserReceipt   ParserReceipt     `json:"parserReceipt"`
	EvidenceUnits   []EvidenceUnit    `json:"evidenceUnits"`
	Chunks          []StructuredChunk `json:"chunks"`
}

// Validate prevents a retrieval chunk from citing another document version.
func (chunk StructuredChunk) Validate(evidenceByID map[string]EvidenceUnit) error {
	if strings.TrimSpace(chunk.DocumentVersion) == "" {
		return fmt.Errorf("document version is required")
	}
	for _, evidenceID := range chunk.EvidenceIDs {
		evidence, ok := evidenceByID[evidenceID]
		if !ok {
			return fmt.Errorf("evidence %q does not exist", evidenceID)
		}
		if evidence.DocumentVersion != chunk.DocumentVersion {
			return fmt.Errorf("evidence %q belongs to document version %q, not %q", evidenceID, evidence.DocumentVersion, chunk.DocumentVersion)
		}
	}
	return nil
}

// Citation is provenance returned with an answer or search result.
type Citation struct {
	EvidenceID      string       `json:"evidenceId"`
	Label           string       `json:"label"`
	DocumentVersion string       `json:"documentVersion"`
	Modality        string       `json:"modality"`
	Page            int          `json:"page,omitempty"`
	Slide           int          `json:"slide,omitempty"`
	Sheet           string       `json:"sheet,omitempty"`
	BBox            *BoundingBox `json:"bbox,omitempty"`
	Excerpt         string       `json:"excerpt"`
	SourcePath      string       `json:"sourcePath"`
}

// NewCitation retains the source location needed for page-level evidence.
func NewCitation(evidence EvidenceUnit) Citation {
	label := evidence.ID
	if evidence.Page > 0 {
		label = fmt.Sprintf("page %d", evidence.Page)
	} else if evidence.Slide > 0 {
		label = fmt.Sprintf("slide %d", evidence.Slide)
	} else if evidence.Sheet != "" {
		label = evidence.Sheet
	}
	return Citation{
		EvidenceID:      evidence.ID,
		Label:           label,
		DocumentVersion: evidence.DocumentVersion,
		Modality:        evidence.Modality,
		Page:            evidence.Page,
		Slide:           evidence.Slide,
		Sheet:           evidence.Sheet,
		BBox:            evidence.BBox,
		Excerpt:         evidence.Text,
		SourcePath:      evidence.AssetPath,
	}
}
