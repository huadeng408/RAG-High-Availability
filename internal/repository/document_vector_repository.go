package repository

import (
	"pai-smart-go/internal/model"

	"gorm.io/gorm"
)

type DocumentVectorRepository interface {
	BatchCreate(vectors []*model.DocumentVector) error
	FindByFileMD5(fileMD5 string) ([]*model.DocumentVector, error)
	FindByFileMD5Range(fileMD5 string, offset, limit int) ([]*model.DocumentVector, error)
	CountByFileMD5(fileMD5 string) (int64, error)
	DeleteByFileMD5(fileMD5 string) error
}

type documentVectorRepository struct {
	db *gorm.DB
}

func NewDocumentVectorRepository(db *gorm.DB) DocumentVectorRepository {
	return &documentVectorRepository{db: db}
}

func (r *documentVectorRepository) BatchCreate(vectors []*model.DocumentVector) error {
	if len(vectors) == 0 {
		return nil
	}
	return r.db.CreateInBatches(vectors, 100).Error
}

func (r *documentVectorRepository) FindByFileMD5(fileMD5 string) ([]*model.DocumentVector, error) {
	var vectors []*model.DocumentVector
	err := r.db.Where("file_md5 = ?", fileMD5).Order("chunk_id asc").Find(&vectors).Error
	return vectors, err
}

func (r *documentVectorRepository) FindByFileMD5Range(fileMD5 string, offset, limit int) ([]*model.DocumentVector, error) {
	if limit <= 0 {
		return []*model.DocumentVector{}, nil
	}
	var vectors []*model.DocumentVector
	err := r.db.
		Where("file_md5 = ?", fileMD5).
		Order("chunk_id asc").
		Offset(offset).
		Limit(limit).
		Find(&vectors).Error
	return vectors, err
}

func (r *documentVectorRepository) CountByFileMD5(fileMD5 string) (int64, error) {
	var count int64
	err := r.db.Model(&model.DocumentVector{}).Where("file_md5 = ?", fileMD5).Count(&count).Error
	return count, err
}

func (r *documentVectorRepository) DeleteByFileMD5(fileMD5 string) error {
	return r.db.Where("file_md5 = ?", fileMD5).Delete(&model.DocumentVector{}).Error
}
