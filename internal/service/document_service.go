// Package service contains application business logic.
package service

import (
	"context"
	"errors"
	"net/url"
	"strings"
	"time"

	"github.com/huadeng408/RAG-High-Availability/internal/config"
	"github.com/huadeng408/RAG-High-Availability/internal/model"
	"github.com/huadeng408/RAG-High-Availability/internal/repository"
	"github.com/huadeng408/RAG-High-Availability/pkg/database"
	"github.com/huadeng408/RAG-High-Availability/pkg/es"
	"github.com/huadeng408/RAG-High-Availability/pkg/objectpath"
	"github.com/huadeng408/RAG-High-Availability/pkg/storage"
	"github.com/huadeng408/RAG-High-Availability/pkg/tika"

	"github.com/minio/minio-go/v7"
)

const PipelineStatusSearchable = "SEARCHABLE"

var pipelineStages = []string{"parse", "chunk", "embed", "index"}

// PipelineStageStatus is the aggregated status of one logical pipeline stage.
type PipelineStageStatus struct {
	Stage          string     `json:"stage"`
	Status         string     `json:"status"`
	AttemptCount   int        `json:"attemptCount"`
	RetryCount     int        `json:"retryCount"`
	LastError      string     `json:"lastError,omitempty"`
	ErrorClass     string     `json:"errorClass,omitempty"`
	LastTraceID    string     `json:"lastTraceId,omitempty"`
	NextAttemptAt  *time.Time `json:"nextAttemptAt,omitempty"`
	DLQMessageID   string     `json:"dlqMessageId,omitempty"`
	DeadLetteredAt *time.Time `json:"deadLetteredAt,omitempty"`
	ReplayCount    int        `json:"replayCount"`
	LastReplayedAt *time.Time `json:"lastReplayedAt,omitempty"`
	UpdatedAt      time.Time  `json:"updatedAt"`
}

// PipelineStatus describes the version currently tracked by the ingestion pipeline.
type PipelineStatus struct {
	FileMD5         string                `json:"fileMd5"`
	DocumentVersion string                `json:"documentVersion,omitempty"`
	Status          string                `json:"status"`
	Stages          []PipelineStageStatus `json:"stages"`
}

// FileUploadDTO is returned to the frontend with resolved organization tag names.
type FileUploadDTO struct {
	model.FileUpload
	OrgTagName string `json:"orgTagName"`
}

// DownloadInfoDTO contains temporary download information for a file.
type DownloadInfoDTO struct {
	FileName    string `json:"fileName"`
	DownloadURL string `json:"downloadUrl"`
	FileSize    int64  `json:"fileSize"`
}

// PreviewInfoDTO contains text preview information for a file.
type PreviewInfoDTO struct {
	FileName string `json:"fileName"`
	Content  string `json:"content"`
	FileSize int64  `json:"fileSize"`
}

// DocumentService defines document management operations.
type DocumentService interface {
	ListAccessibleFiles(user *model.User) ([]model.FileUpload, error)
	ListUploadedFiles(userID uint) ([]FileUploadDTO, error)
	DeleteDocument(fileMD5 string, user *model.User) error
	GenerateDownloadURL(fileName string, user *model.User) (*DownloadInfoDTO, error)
	GetFilePreviewContent(fileName string, user *model.User) (*PreviewInfoDTO, error)
	GetPipelineStatus(fileMD5 string, user *model.User) (*PipelineStatus, error)
}

// documentService implements document operations.
type documentService struct {
	uploadRepo       repository.UploadRepository
	userRepo         repository.UserRepository
	orgTagRepo       repository.OrgTagRepository
	docVectorRepo    repository.DocumentVectorRepository
	pipelineTaskRepo repository.PipelineTaskRepository
	minioCfg         config.MinIOConfig
	esIndexName      string
	tikaClient       *tika.Client
}

// NewDocumentService creates a DocumentService.
func NewDocumentService(
	uploadRepo repository.UploadRepository,
	userRepo repository.UserRepository,
	orgTagRepo repository.OrgTagRepository,
	docVectorRepo repository.DocumentVectorRepository,
	pipelineTaskRepo repository.PipelineTaskRepository,
	minioCfg config.MinIOConfig,
	esIndexName string,
	tikaClient *tika.Client,
) DocumentService {
	return &documentService{
		uploadRepo:       uploadRepo,
		userRepo:         userRepo,
		orgTagRepo:       orgTagRepo,
		docVectorRepo:    docVectorRepo,
		pipelineTaskRepo: pipelineTaskRepo,
		minioCfg:         minioCfg,
		esIndexName:      esIndexName,
		tikaClient:       tikaClient,
	}
}

// ListAccessibleFiles returns files the user can access.
func (s *documentService) ListAccessibleFiles(user *model.User) ([]model.FileUpload, error) {
	orgTags := strings.Split(user.OrgTags, ",")
	return s.uploadRepo.FindAccessibleFiles(user.ID, orgTags)
}

// ListUploadedFiles returns files uploaded by one user.
func (s *documentService) ListUploadedFiles(userID uint) ([]FileUploadDTO, error) {
	files, err := s.uploadRepo.FindFilesByUserID(userID)
	if err != nil {
		return nil, err
	}

	dtos, err := s.mapFileUploadsToDTOs(files)
	if err != nil {
		return nil, err
	}

	return dtos, nil
}

// GetPipelineStatus returns durable stage state after checking file ownership.
func (s *documentService) GetPipelineStatus(fileMD5 string, user *model.User) (*PipelineStatus, error) {
	fileMD5 = strings.TrimSpace(fileMD5)
	if fileMD5 == "" {
		return nil, errors.New("file md5 cannot be empty")
	}
	if user == nil {
		return nil, errors.New("user is required")
	}
	record, err := s.uploadRepo.GetFileUploadRecordByMD5(fileMD5)
	if err != nil {
		return nil, err
	}
	if record.UserID != user.ID && user.Role != "ADMIN" {
		return nil, errors.New("permission denied")
	}
	lister, ok := s.pipelineTaskRepo.(interface {
		ListByFileMD5(string) ([]model.PipelineTask, error)
	})
	if !ok {
		return nil, errors.New("pipeline status is not supported")
	}
	tasks, err := lister.ListByFileMD5(fileMD5)
	if err != nil {
		return nil, err
	}
	status := AggregatePipelineStatus(fileMD5, tasks)
	return &status, nil
}

// AggregatePipelineStatus combines per-window tasks for the newest document version.
// A version is searchable only when every logical stage has succeeded.
func AggregatePipelineStatus(fileMD5 string, tasks []model.PipelineTask) PipelineStatus {
	status := PipelineStatus{FileMD5: fileMD5, Status: model.PipelineStatusPending, Stages: make([]PipelineStageStatus, 0, len(pipelineStages))}
	version := latestPipelineVersion(tasks)
	status.DocumentVersion = version
	byStage := make(map[string][]model.PipelineTask, len(pipelineStages))
	for _, task := range tasks {
		isUploadParse := task.Stage == "parse" && task.DocumentVersion == "upload:"+fileMD5
		if version == "" || task.DocumentVersion == version || isUploadParse {
			byStage[task.Stage] = append(byStage[task.Stage], task)
		}
	}

	allSuccess := true
	anyProcessing := false
	anyFailed := false
	for _, stage := range pipelineStages {
		stageStatus := aggregatePipelineStage(stage, byStage[stage])
		status.Stages = append(status.Stages, stageStatus)
		switch stageStatus.Status {
		case model.PipelineStatusSuccess:
		case model.PipelineStatusProcessing:
			allSuccess = false
			anyProcessing = true
		case model.PipelineStatusFailed:
			allSuccess = false
			anyFailed = true
		default:
			allSuccess = false
		}
	}
	if allSuccess {
		status.Status = PipelineStatusSearchable
	} else if anyProcessing {
		status.Status = model.PipelineStatusProcessing
	} else if anyFailed {
		status.Status = model.PipelineStatusFailed
	}
	return status
}

func latestPipelineVersion(tasks []model.PipelineTask) string {
	var latest model.PipelineTask
	for _, task := range tasks {
		if strings.TrimSpace(task.DocumentVersion) == "" {
			continue
		}
		if latest.DocumentVersion == "" || task.CreatedAt.After(latest.CreatedAt) ||
			(task.CreatedAt.Equal(latest.CreatedAt) && task.DocumentVersion > latest.DocumentVersion) {
			latest = task
		}
	}
	return latest.DocumentVersion
}

func aggregatePipelineStage(stage string, tasks []model.PipelineTask) PipelineStageStatus {
	result := PipelineStageStatus{Stage: stage, Status: model.PipelineStatusPending}
	if len(tasks) == 0 {
		return result
	}
	for _, task := range tasks {
		result.AttemptCount += task.AttemptCount
	}
	latest := tasks[0]
	latestDeadLetter := tasks[0]
	for _, task := range tasks {
		if task.RetryCount > result.RetryCount {
			result.RetryCount = task.RetryCount
		}
		result.ReplayCount += task.ReplayCount
		if task.UpdatedAt.After(latest.UpdatedAt) {
			latest = task
		}
		if task.DeadLetteredAt != nil && (latestDeadLetter.DeadLetteredAt == nil || task.DeadLetteredAt.After(*latestDeadLetter.DeadLetteredAt)) {
			latestDeadLetter = task
		}
	}
	result.LastError = latest.LastError
	result.ErrorClass = latest.ErrorClass
	result.LastTraceID = latest.LastTraceID
	result.NextAttemptAt = latest.NextAttemptAt
	result.UpdatedAt = latest.UpdatedAt
	if latestDeadLetter.DeadLetteredAt != nil {
		result.DLQMessageID = latestDeadLetter.DLQMessageID
		result.DeadLetteredAt = latestDeadLetter.DeadLetteredAt
		result.LastReplayedAt = latestDeadLetter.LastReplayedAt
	}

	anyProcessing := false
	anyFailed := false
	allSuccess := true
	for _, task := range tasks {
		switch task.Status {
		case model.PipelineStatusProcessing:
			anyProcessing = true
			allSuccess = false
		case model.PipelineStatusFailed:
			anyFailed = true
			allSuccess = false
		case model.PipelineStatusSuccess:
		default:
			allSuccess = false
		}
	}
	if anyProcessing {
		result.Status = model.PipelineStatusProcessing
	} else if anyFailed {
		result.Status = model.PipelineStatusFailed
	} else if allSuccess {
		result.Status = model.PipelineStatusSuccess
	}
	return result
}

// DeleteDocument deletes a document and its derived search artifacts.
func (s *documentService) DeleteDocument(fileMD5 string, user *model.User) error {
	record, err := s.uploadRepo.GetFileUploadRecordByMD5(fileMD5)
	if err != nil {
		return errors.New("file not found or not accessible")
	}

	if record.UserID != user.ID && user.Role != "ADMIN" {
		return errors.New("permission denied to delete this file")
	}

	ctx := context.Background()
	if err := es.DeleteDocumentsByFileMD5(ctx, s.esIndexName, fileMD5); err != nil {
		return err
	}
	if err := s.docVectorRepo.DeleteByFileMD5(fileMD5); err != nil {
		return err
	}
	if err := s.uploadRepo.DeleteChunkInfoByFileMD5(fileMD5); err != nil {
		return err
	}
	if err := s.pipelineTaskRepo.DeleteByFileMD5(fileMD5); err != nil {
		return err
	}

	_ = s.uploadRepo.DeleteUploadMark(ctx, fileMD5, record.UserID)
	_ = database.RDB.Del(ctx, "pipeline:embeddings:"+fileMD5).Err()

	objectName := objectpath.MergedObjectName(record.FileMD5, record.FileName)
	_ = storage.MinioClient.RemoveObject(ctx, s.minioCfg.BucketName, objectName, minio.RemoveObjectOptions{})
	_ = storage.MinioClient.RemoveObject(ctx, s.minioCfg.BucketName, objectpath.ParsedObjectName(fileMD5), minio.RemoveObjectOptions{})

	if user.Role == "ADMIN" {
		return s.uploadRepo.DeleteFileUploadRecordByMD5(fileMD5)
	}
	return s.uploadRepo.DeleteFileUploadRecord(fileMD5, record.UserID)
}

// GenerateDownloadURL creates a temporary download URL for a file.
func (s *documentService) GenerateDownloadURL(fileName string, user *model.User) (*DownloadInfoDTO, error) {
	files, err := s.ListAccessibleFiles(user)
	if err != nil {
		return nil, err
	}

	var targetFile *model.FileUpload
	for i := range files {
		if files[i].FileName == fileName {
			targetFile = &files[i]
			break
		}
	}

	if targetFile == nil {
		return nil, errors.New("file not found or not accessible")
	}

	expiry := time.Hour
	objectName := objectpath.MergedObjectName(targetFile.FileMD5, targetFile.FileName)
	presignedURL, err := storage.MinioClient.PresignedGetObject(context.Background(), s.minioCfg.BucketName, objectName, expiry, url.Values{})
	if err != nil {
		return nil, err
	}

	return &DownloadInfoDTO{
		FileName:    targetFile.FileName,
		DownloadURL: presignedURL.String(),
		FileSize:    targetFile.TotalSize,
	}, nil
}

// GetFilePreviewContent extracts text preview content for a file.
func (s *documentService) GetFilePreviewContent(fileName string, user *model.User) (*PreviewInfoDTO, error) {
	files, err := s.ListAccessibleFiles(user)
	if err != nil {
		return nil, err
	}

	var targetFile *model.FileUpload
	for i := range files {
		if files[i].FileName == fileName {
			targetFile = &files[i]
			break
		}
	}

	if targetFile == nil {
		return nil, errors.New("file not found or not accessible")
	}

	objectName := objectpath.MergedObjectName(targetFile.FileMD5, targetFile.FileName)
	object, err := storage.MinioClient.GetObject(context.Background(), s.minioCfg.BucketName, objectName, minio.GetObjectOptions{})
	if err != nil {
		return nil, err
	}
	defer object.Close()

	content, err := s.tikaClient.ExtractText(object, fileName)
	if err != nil {
		return nil, err
	}

	return &PreviewInfoDTO{
		FileName: targetFile.FileName,
		Content:  content,
		FileSize: targetFile.TotalSize,
	}, nil
}

// mapFileUploadsToDTOs resolves organization tag names for file uploads.
func (s *documentService) mapFileUploadsToDTOs(files []model.FileUpload) ([]FileUploadDTO, error) {
	if len(files) == 0 {
		return []FileUploadDTO{}, nil
	}

	tagIDs := make(map[string]struct{})
	for _, file := range files {
		if file.OrgTag != "" {
			tagIDs[file.OrgTag] = struct{}{}
		}
	}

	tagIDList := make([]string, 0, len(tagIDs))
	for id := range tagIDs {
		tagIDList = append(tagIDList, id)
	}

	tags, err := s.orgTagRepo.FindBatchByIDs(tagIDList)
	if err != nil {
		return nil, err
	}

	tagMap := make(map[string]string)
	for _, tag := range tags {
		tagMap[tag.TagID] = tag.Name
	}

	dtos := make([]FileUploadDTO, len(files))
	for i, file := range files {
		dtos[i] = FileUploadDTO{
			FileUpload: file,
			OrgTagName: tagMap[file.OrgTag],
		}
	}

	return dtos, nil
}
