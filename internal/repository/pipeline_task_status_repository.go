package repository

import "github.com/huadeng408/RAG-High-Availability/internal/model"

// ListByFileMD5 lists all versioned stage tasks for one uploaded file.
// It lives beside, rather than inside, the legacy repository contract so
// callers can remain compatible with older task repository implementations.
func (r *pipelineTaskRepository) ListByFileMD5(fileMD5 string) ([]model.PipelineTask, error) {
	var tasks []model.PipelineTask
	err := r.db.Where("file_md5 = ?", fileMD5).Order("created_at asc, id asc").Find(&tasks).Error
	return tasks, err
}
