// Package model contains persistent models and DTOs.
package model

// DocumentVector 对应于数据库中的 document_vectors 表。
// 它的结构与 Java 项目中的 DocumentVector 实体完全一致。
type DocumentVector struct {
	VectorID        uint         `gorm:"primaryKey;autoIncrement;column:vector_id"`
	FileMD5         string       `gorm:"type:varchar(32);not null;index;column:file_md5"`
	DocumentVersion string       `gorm:"type:varchar(96);not null;index;column:document_version"`
	ChunkID         int          `gorm:"not null;column:chunk_id"`
	TextContent     string       `gorm:"type:text;column:text_content"`
	ModelVersion    string       `gorm:"type:varchar(128);column:model_version"`
	UserID          uint         `gorm:"not null;column:user_id"`
	OrgTag          string       `gorm:"type:varchar(50);column:org_tag"`
	IsPublic        bool         `gorm:"not null;default:false;column:is_public"`
	Modality        string       `gorm:"type:varchar(32);column:modality"`
	Page            int          `gorm:"column:page_number"`
	Slide           int          `gorm:"column:slide_number"`
	Sheet           string       `gorm:"type:varchar(255);column:sheet_name"`
	RowStart        int          `gorm:"column:row_start"`
	RowEnd          int          `gorm:"column:row_end"`
	EvidenceIDs     []string     `gorm:"serializer:json;column:evidence_ids"`
	BBox            *BoundingBox `gorm:"serializer:json;column:bbox"`
}

// TableName handles table name.
func (DocumentVector) TableName() string {
	return "document_vectors"
}
