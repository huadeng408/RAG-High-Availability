package repository

import (
	"encoding/json"

	"github.com/huadeng408/RAG-High-Availability/internal/model"
	"gorm.io/gorm"
)

// EvidenceRepository persists citeable source evidence separately from chunks.
type EvidenceRepository interface {
	ReplaceForVersion(documentVersion string, evidence []model.EvidenceUnit) error
	ListByVersion(documentVersion string) ([]model.EvidenceUnit, error)
}

type evidenceRepository struct{ db *gorm.DB }

type evidenceRecord struct {
	ID              string `gorm:"column:evidence_id;primaryKey"`
	DocumentVersion string `gorm:"column:document_version;index"`
	Modality        string `gorm:"column:modality"`
	ElementType     string `gorm:"column:element_type"`
	Page            int    `gorm:"column:page_number"`
	Slide           int    `gorm:"column:slide_number"`
	Sheet           string `gorm:"column:sheet_name"`
	RowStart        int    `gorm:"column:row_start"`
	RowEnd          int    `gorm:"column:row_end"`
	HeadingPathJSON []byte `gorm:"column:heading_path"`
	HeaderJSON      []byte `gorm:"column:header"`
	BBoxJSON        []byte `gorm:"column:bbox"`
	Text            string `gorm:"column:text_content"`
	ParserName      string `gorm:"column:parser_name"`
	ParserVersion   string `gorm:"column:parser_version"`
	AssetPath       string `gorm:"column:asset_path"`
	OwnerID         uint   `gorm:"column:owner_id"`
	OrgTag          string `gorm:"column:org_tag"`
	IsPublic        bool   `gorm:"column:is_public"`
}

func (evidenceRecord) TableName() string { return "evidence_units" }

// NewEvidenceRepository creates an evidence repository.
func NewEvidenceRepository(db *gorm.DB) EvidenceRepository { return &evidenceRepository{db: db} }

func (r *evidenceRepository) ReplaceForVersion(documentVersion string, evidence []model.EvidenceUnit) error {
	return r.db.Transaction(func(tx *gorm.DB) error {
		if err := tx.Where("document_version = ?", documentVersion).Delete(&evidenceRecord{}).Error; err != nil {
			return err
		}
		for _, unit := range evidence {
			headingPath, err := json.Marshal(unit.HeadingPath)
			if err != nil {
				return err
			}
			header, err := json.Marshal(unit.Header)
			if err != nil {
				return err
			}
			bbox, err := json.Marshal(unit.BBox)
			if err != nil {
				return err
			}
			record := evidenceRecord{ID: unit.ID, DocumentVersion: documentVersion, Modality: unit.Modality, ElementType: unit.ElementType, Page: unit.Page, Slide: unit.Slide, Sheet: unit.Sheet, RowStart: unit.RowStart, RowEnd: unit.RowEnd, HeadingPathJSON: headingPath, HeaderJSON: header, BBoxJSON: bbox, Text: unit.Text, ParserName: unit.ParserName, ParserVersion: unit.ParserVersion, AssetPath: unit.AssetPath}
			if err := tx.Create(&record).Error; err != nil {
				return err
			}
		}
		return nil
	})
}

func (r *evidenceRepository) ListByVersion(documentVersion string) ([]model.EvidenceUnit, error) {
	var records []evidenceRecord
	if err := r.db.Where("document_version = ?", documentVersion).Order("evidence_id").Find(&records).Error; err != nil {
		return nil, err
	}
	evidence := make([]model.EvidenceUnit, 0, len(records))
	for _, record := range records {
		var headingPath []string
		if len(record.HeadingPathJSON) > 0 && string(record.HeadingPathJSON) != "null" {
			if err := json.Unmarshal(record.HeadingPathJSON, &headingPath); err != nil {
				return nil, err
			}
		}
		var header []string
		if len(record.HeaderJSON) > 0 && string(record.HeaderJSON) != "null" {
			if err := json.Unmarshal(record.HeaderJSON, &header); err != nil {
				return nil, err
			}
		}
		var bbox *model.BoundingBox
		if len(record.BBoxJSON) > 0 && string(record.BBoxJSON) != "null" {
			if err := json.Unmarshal(record.BBoxJSON, &bbox); err != nil {
				return nil, err
			}
		}
		evidence = append(evidence, model.EvidenceUnit{ID: record.ID, DocumentVersion: record.DocumentVersion, Modality: record.Modality, ElementType: record.ElementType, Page: record.Page, Slide: record.Slide, Sheet: record.Sheet, RowStart: record.RowStart, RowEnd: record.RowEnd, HeadingPath: headingPath, Header: header, BBox: bbox, Text: record.Text, ParserName: record.ParserName, ParserVersion: record.ParserVersion, AssetPath: record.AssetPath})
	}
	return evidence, nil
}
