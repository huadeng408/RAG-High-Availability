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
	Modality        string
	ElementType     string
	Page            int
	Slide           int
	Sheet           string
	BBoxJSON        []byte `gorm:"column:bbox"`
	Text            string `gorm:"column:text_content"`
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
			bbox, err := json.Marshal(unit.BBox)
			if err != nil {
				return err
			}
			record := evidenceRecord{ID: unit.ID, DocumentVersion: documentVersion, Modality: unit.Modality, ElementType: unit.ElementType, Page: unit.Page, Slide: unit.Slide, Sheet: unit.Sheet, BBoxJSON: bbox, Text: unit.Text}
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
		var bbox *model.BoundingBox
		if len(record.BBoxJSON) > 0 && string(record.BBoxJSON) != "null" {
			if err := json.Unmarshal(record.BBoxJSON, &bbox); err != nil {
				return nil, err
			}
		}
		evidence = append(evidence, model.EvidenceUnit{ID: record.ID, DocumentVersion: record.DocumentVersion, Modality: record.Modality, ElementType: record.ElementType, Page: record.Page, Slide: record.Slide, Sheet: record.Sheet, BBox: bbox, Text: record.Text})
	}
	return evidence, nil
}
