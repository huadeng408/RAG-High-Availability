package repository

import (
	"crypto/sha256"
	"fmt"

	"github.com/huadeng408/RAG-High-Availability/internal/model"
	"gorm.io/gorm"
	"gorm.io/gorm/clause"
)

// DocumentVersionRepository persists immutable upload versions.
type DocumentVersionRepository interface {
	CreateForUpload(source model.DocumentSource, contents []byte, parserName, parserVersion string) (*model.DocumentVersion, error)
}

type documentVersionRepository struct{ db *gorm.DB }

// NewDocumentVersionRepository creates a version repository.
func NewDocumentVersionRepository(db *gorm.DB) DocumentVersionRepository {
	return &documentVersionRepository{db: db}
}

// CreateForUpload inserts or loads the one immutable version for source content.
func (r *documentVersionRepository) CreateForUpload(source model.DocumentSource, contents []byte, parserName, parserVersion string) (*model.DocumentVersion, error) {
	contentHash := fmt.Sprintf("%x", sha256.Sum256(contents))
	versionID := fmt.Sprintf("%x", sha256.Sum256([]byte(source.SourceID+"\x00"+contentHash)))
	version := &model.DocumentVersion{
		DocumentVersionID: versionID,
		SourceID:          source.SourceID,
		ContentSHA256:     contentHash,
		ParserName:        parserName,
		ParserVersion:     parserVersion,
	}
	result := r.db.Clauses(clause.OnConflict{
		Columns:   []clause.Column{{Name: "source_id"}, {Name: "content_sha256"}},
		DoNothing: true,
	}).Create(version)
	if result.Error != nil {
		return nil, result.Error
	}
	if result.RowsAffected > 0 {
		return version, nil
	}
	if err := r.db.Where("source_id = ? AND content_sha256 = ?", source.SourceID, contentHash).First(version).Error; err != nil {
		return nil, err
	}
	return version, nil
}
