package service

import (
	"bytes"
	"context"
	"crypto/md5"
	"encoding/hex"
	"errors"
	"fmt"
	"io"
	"math"
	"mime/multipart"
	"sort"
	"strings"

	"pai-smart-go/internal/config"
	"pai-smart-go/internal/model"
	"pai-smart-go/internal/repository"
	"pai-smart-go/pkg/kafka"
	"pai-smart-go/pkg/log"
	"pai-smart-go/pkg/objectpath"
	"pai-smart-go/pkg/storage"
	"pai-smart-go/pkg/tasks"

	"github.com/minio/minio-go/v7"
	"gorm.io/gorm"
)

const DefaultChunkSize = 5 * 1024 * 1024

type UploadService interface {
	CheckFile(ctx context.Context, fileMD5 string, userID uint) (bool, []int, error)
	UploadChunk(ctx context.Context, fileMD5, fileName string, totalSize int64, chunkIndex int, file multipart.File, chunkMD5 string, userID uint, orgTag string, isPublic bool) (uploadedChunks []int, totalChunks int, err error)
	MergeChunks(ctx context.Context, fileMD5, fileName string, userID uint) (string, error)
	GetUploadStatus(ctx context.Context, fileMD5 string, userID uint) (fileName string, fileType string, uploadedChunks []int, totalChunks int, err error)
	GetSupportedFileTypes() (map[string]interface{}, error)
	FastUpload(ctx context.Context, fileMD5 string, userID uint) (bool, error)
}

type uploadService struct {
	uploadRepo repository.UploadRepository
	userRepo   repository.UserRepository
	minioCfg   config.MinIOConfig
}

func NewUploadService(uploadRepo repository.UploadRepository, userRepo repository.UserRepository, minioCfg config.MinIOConfig) UploadService {
	return &uploadService{uploadRepo: uploadRepo, userRepo: userRepo, minioCfg: minioCfg}
}

func (s *uploadService) CheckFile(ctx context.Context, fileMD5 string, userID uint) (bool, []int, error) {
	record, err := s.uploadRepo.GetFileUploadRecord(fileMD5, userID)
	if err != nil {
		if errors.Is(err, gorm.ErrRecordNotFound) {
			return false, nil, nil
		}
		return false, nil, err
	}
	if record.Status == 1 {
		return true, nil, nil
	}
	totalChunks := s.calculateTotalChunks(record.TotalSize)
	uploaded, err := s.getUploadedChunks(ctx, fileMD5, userID, totalChunks)
	if err != nil {
		return false, nil, err
	}
	return false, uploaded, nil
}

func (s *uploadService) UploadChunk(ctx context.Context, fileMD5, fileName string, totalSize int64, chunkIndex int, file multipart.File, chunkMD5 string, userID uint, orgTag string, isPublic bool) ([]int, int, error) {
	chunkMD5 = strings.ToLower(strings.TrimSpace(chunkMD5))
	if chunkMD5 == "" {
		return nil, 0, errors.New("chunkMd5 is required")
	}
	if chunkIndex < 0 {
		return nil, 0, errors.New("invalid chunkIndex")
	}

	if chunkIndex == 0 {
		supportedTypes, _ := s.GetSupportedFileTypes()
		extensions, ok := supportedTypes["supportedExtensions"].([]string)
		if !ok {
			return nil, 0, errors.New("invalid supported types configuration")
		}
		valid := false
		for _, ext := range extensions {
			if strings.HasSuffix(strings.ToLower(fileName), ext) {
				valid = true
				break
			}
		}
		if !valid {
			return nil, 0, fmt.Errorf("unsupported file type for %s", fileName)
		}
	}

	record, err := s.uploadRepo.GetFileUploadRecord(fileMD5, userID)
	if errors.Is(err, gorm.ErrRecordNotFound) {
		if orgTag == "" {
			user, userErr := s.userRepo.FindByID(userID)
			if userErr != nil {
				return nil, 0, userErr
			}
			orgTag = user.PrimaryOrg
		}
		record = &model.FileUpload{
			FileMD5:   fileMD5,
			FileName:  fileName,
			TotalSize: totalSize,
			Status:    0,
			UserID:    userID,
			OrgTag:    orgTag,
			IsPublic:  isPublic,
		}
		if err := s.uploadRepo.CreateFileUploadRecord(record); err != nil {
			return nil, 0, err
		}
	} else if err != nil {
		return nil, 0, err
	}

	totalChunks := s.calculateTotalChunks(record.TotalSize)
	if chunkIndex >= totalChunks {
		return nil, 0, fmt.Errorf("chunkIndex out of range: %d >= %d", chunkIndex, totalChunks)
	}

	isUploaded, err := s.uploadRepo.IsChunkUploaded(ctx, fileMD5, userID, chunkIndex)
	if err != nil {
		return nil, 0, fmt.Errorf("failed to check chunk status from redis: %w", err)
	}
	if isUploaded {
		uploaded, err := s.getUploadedChunks(ctx, fileMD5, userID, totalChunks)
		if err != nil {
			return nil, 0, err
		}
		return uploaded, totalChunks, nil
	}
	// Redis is an acceleration layer. If it misses, check DB chunk metadata to keep idempotency.
	chunkRecord, err := s.uploadRepo.GetChunkInfoRecord(fileMD5, chunkIndex)
	if err == nil {
		dbChunkMD5 := strings.ToLower(strings.TrimSpace(chunkRecord.ChunkMD5))
		if dbChunkMD5 != chunkMD5 {
			return nil, 0, fmt.Errorf("chunk md5 conflict at index=%d: existing=%s request=%s", chunkIndex, dbChunkMD5, chunkMD5)
		}
		if markErr := s.uploadRepo.MarkChunkUploaded(ctx, fileMD5, userID, chunkIndex); markErr != nil {
			return nil, 0, markErr
		}
		uploaded, upErr := s.getUploadedChunks(ctx, fileMD5, userID, totalChunks)
		if upErr != nil {
			return nil, 0, upErr
		}
		return uploaded, totalChunks, nil
	}
	if !errors.Is(err, gorm.ErrRecordNotFound) {
		return nil, 0, err
	}

	chunkBytes, err := io.ReadAll(file)
	if err != nil {
		return nil, 0, fmt.Errorf("failed to read chunk: %w", err)
	}
	actualChunkMD5 := calculateMD5Hex(chunkBytes)
	if actualChunkMD5 != chunkMD5 {
		return nil, 0, fmt.Errorf("chunk md5 mismatch: expect=%s actual=%s", chunkMD5, actualChunkMD5)
	}

	objectName := fmt.Sprintf("chunks/%s/%d", fileMD5, chunkIndex)
	_, err = storage.MinioClient.PutObject(ctx, s.minioCfg.BucketName, objectName, bytes.NewReader(chunkBytes), int64(len(chunkBytes)), minio.PutObjectOptions{})
	if err != nil {
		return nil, 0, err
	}
	if err := s.verifyChunkObjectMD5(ctx, objectName, chunkMD5); err != nil {
		return nil, 0, err
	}

	if err := s.uploadRepo.UpsertChunkInfoRecord(&model.ChunkInfo{
		FileMD5:     fileMD5,
		ChunkIndex:  chunkIndex,
		ChunkMD5:    chunkMD5,
		StoragePath: objectName,
	}); err != nil {
		return nil, 0, err
	}

	if err := s.uploadRepo.MarkChunkUploaded(ctx, fileMD5, userID, chunkIndex); err != nil {
		return nil, 0, err
	}

	uploaded, err := s.getUploadedChunks(ctx, fileMD5, userID, totalChunks)
	if err != nil {
		return nil, 0, err
	}
	return uploaded, totalChunks, nil
}

func (s *uploadService) MergeChunks(ctx context.Context, fileMD5, fileName string, userID uint) (string, error) {
	record, err := s.uploadRepo.GetFileUploadRecord(fileMD5, userID)
	if err != nil {
		return "", err
	}

	totalChunks := s.calculateTotalChunks(record.TotalSize)
	uploaded, err := s.getUploadedChunks(ctx, fileMD5, userID, totalChunks)
	if err != nil {
		return "", fmt.Errorf("failed to get uploaded chunks: %w", err)
	}
	if len(uploaded) < totalChunks {
		return "", fmt.Errorf("chunks are incomplete, expected=%d actual=%d", totalChunks, len(uploaded))
	}
	if err := s.verifyAllChunkIntegrity(ctx, fileMD5, totalChunks); err != nil {
		return "", err
	}

	destObjectName := objectpath.MergedObjectName(fileMD5, fileName)
	if totalChunks == 1 {
		src := minio.CopySrcOptions{Bucket: s.minioCfg.BucketName, Object: fmt.Sprintf("chunks/%s/0", fileMD5)}
		dst := minio.CopyDestOptions{Bucket: s.minioCfg.BucketName, Object: destObjectName}
		if _, err := storage.MinioClient.CopyObject(context.Background(), dst, src); err != nil {
			return "", fmt.Errorf("failed to copy single chunk object: %w", err)
		}
	} else {
		srcs := make([]minio.CopySrcOptions, 0, totalChunks)
		for i := 0; i < totalChunks; i++ {
			srcs = append(srcs, minio.CopySrcOptions{Bucket: s.minioCfg.BucketName, Object: fmt.Sprintf("chunks/%s/%d", fileMD5, i)})
		}
		dst := minio.CopyDestOptions{Bucket: s.minioCfg.BucketName, Object: destObjectName}
		if _, err := storage.MinioClient.ComposeObject(context.Background(), dst, srcs...); err != nil {
			return "", err
		}
	}

	if err := s.uploadRepo.UpdateFileUploadStatus(record.ID, 1); err != nil {
		return "", err
	}

	objectURL, _ := storage.GetPresignedURL(s.minioCfg.BucketName, destObjectName, 60*60)
	task := tasks.FileProcessingTask{
		FileMD5:   fileMD5,
		ObjectURL: objectURL,
		FileName:  fileName,
		UserID:    userID,
		OrgTag:    record.OrgTag,
		IsPublic:  record.IsPublic,
		Stage:     tasks.StageParse,
	}
	if err := kafka.ProduceFileTask(task); err != nil {
		log.Errorf("[MergeChunks] failed to produce kafka task: %v", err)
	}

	go func() {
		bgCtx := context.Background()
		if err := s.uploadRepo.DeleteUploadMark(bgCtx, fileMD5, userID); err != nil {
			log.Warnf("[MergeChunks] failed to clear redis upload mark, fileMD5=%s err=%v", fileMD5, err)
		}
		objectsCh := make(chan minio.ObjectInfo)
		go func() {
			defer close(objectsCh)
			for i := 0; i < totalChunks; i++ {
				objectsCh <- minio.ObjectInfo{Key: fmt.Sprintf("chunks/%s/%d", fileMD5, i)}
			}
		}()
		for range storage.MinioClient.RemoveObjects(bgCtx, s.minioCfg.BucketName, objectsCh, minio.RemoveObjectsOptions{}) {
		}
	}()

	return objectURL, nil
}

func (s *uploadService) GetUploadStatus(ctx context.Context, fileMD5 string, userID uint) (string, string, []int, int, error) {
	record, err := s.uploadRepo.GetFileUploadRecord(fileMD5, userID)
	if err != nil {
		return "", "", nil, 0, err
	}
	totalChunks := s.calculateTotalChunks(record.TotalSize)
	uploaded, err := s.getUploadedChunks(ctx, fileMD5, userID, totalChunks)
	if err != nil {
		return "", "", nil, 0, err
	}
	return record.FileName, getFileType(record.FileName), uploaded, totalChunks, nil
}

func (s *uploadService) GetSupportedFileTypes() (map[string]interface{}, error) {
	typeMapping := map[string]string{
		".pdf":  "PDF",
		".doc":  "WORD",
		".docx": "WORD",
		".xls":  "EXCEL",
		".xlsx": "EXCEL",
		".ppt":  "PPT",
		".pptx": "PPT",
		".txt":  "TEXT",
		".md":   "MARKDOWN",
	}
	supportedExtensions := make([]string, 0, len(typeMapping))
	supportedTypes := make([]string, 0, len(typeMapping))
	seen := make(map[string]struct{})
	for ext, typ := range typeMapping {
		supportedExtensions = append(supportedExtensions, ext)
		if _, ok := seen[typ]; !ok {
			seen[typ] = struct{}{}
			supportedTypes = append(supportedTypes, typ)
		}
	}
	return map[string]interface{}{
		"supportedExtensions": supportedExtensions,
		"supportedTypes":      supportedTypes,
		"description":         "Supported document types",
	}, nil
}

func (s *uploadService) FastUpload(ctx context.Context, fileMD5 string, userID uint) (bool, error) {
	record, err := s.uploadRepo.GetFileUploadRecord(fileMD5, userID)
	if err != nil {
		if errors.Is(err, gorm.ErrRecordNotFound) {
			return false, nil
		}
		return false, err
	}
	return record.Status == 1, nil
}

func (s *uploadService) calculateTotalChunks(totalSize int64) int {
	if totalSize == 0 {
		return 0
	}
	return int(math.Ceil(float64(totalSize) / float64(DefaultChunkSize)))
}

func calculateMD5Hex(data []byte) string {
	sum := md5.Sum(data)
	return hex.EncodeToString(sum[:])
}

func (s *uploadService) verifyChunkObjectMD5(ctx context.Context, objectName, expectedMD5 string) error {
	object, err := storage.MinioClient.GetObject(ctx, s.minioCfg.BucketName, objectName, minio.GetObjectOptions{})
	if err != nil {
		return fmt.Errorf("failed to get chunk object %s: %w", objectName, err)
	}
	defer object.Close()

	content, err := io.ReadAll(object)
	if err != nil {
		return fmt.Errorf("failed to read chunk object %s: %w", objectName, err)
	}
	actualMD5 := calculateMD5Hex(content)
	expected := strings.ToLower(strings.TrimSpace(expectedMD5))
	if actualMD5 != expected {
		return fmt.Errorf("chunk md5 mismatch for %s, expect=%s actual=%s", objectName, expected, actualMD5)
	}
	return nil
}

func (s *uploadService) verifyAllChunkIntegrity(ctx context.Context, fileMD5 string, totalChunks int) error {
	chunkRecords, err := s.uploadRepo.GetChunkInfoRecords(fileMD5)
	if err != nil {
		return fmt.Errorf("failed to query chunk records: %w", err)
	}
	if len(chunkRecords) != totalChunks {
		return fmt.Errorf("chunk count mismatch before merge, expected=%d actual=%d", totalChunks, len(chunkRecords))
	}

	byIndex := make(map[int]model.ChunkInfo, len(chunkRecords))
	for _, record := range chunkRecords {
		byIndex[record.ChunkIndex] = record
	}
	for i := 0; i < totalChunks; i++ {
		record, ok := byIndex[i]
		if !ok {
			return fmt.Errorf("missing chunk record for chunk index %d", i)
		}
		if strings.TrimSpace(record.ChunkMD5) == "" {
			return fmt.Errorf("empty chunk md5 for chunk index %d", i)
		}
		objectName := strings.TrimSpace(record.StoragePath)
		if objectName == "" {
			objectName = fmt.Sprintf("chunks/%s/%d", fileMD5, i)
		}
		if err := s.verifyChunkObjectMD5(ctx, objectName, record.ChunkMD5); err != nil {
			return err
		}
	}
	return nil
}

func (s *uploadService) getUploadedChunks(ctx context.Context, fileMD5 string, userID uint, totalChunks int) ([]int, error) {
	uploaded, err := s.uploadRepo.GetUploadedChunksFromRedis(ctx, fileMD5, userID, totalChunks)
	if err != nil {
		return nil, err
	}
	if len(uploaded) > 0 || totalChunks == 0 {
		return uploaded, nil
	}

	chunkRecords, err := s.uploadRepo.GetChunkInfoRecords(fileMD5)
	if err != nil {
		return nil, err
	}
	if len(chunkRecords) == 0 {
		return []int{}, nil
	}

	result := make([]int, 0, len(chunkRecords))
	for _, chunkRecord := range chunkRecords {
		if chunkRecord.ChunkIndex < 0 || chunkRecord.ChunkIndex >= totalChunks {
			continue
		}
		if err := s.uploadRepo.MarkChunkUploaded(ctx, fileMD5, userID, chunkRecord.ChunkIndex); err != nil {
			return nil, err
		}
		result = append(result, chunkRecord.ChunkIndex)
	}
	sort.Ints(result)
	return result, nil
}

func getFileType(fileName string) string {
	if fileName == "" {
		return "UNKNOWN"
	}
	parts := strings.Split(fileName, ".")
	if len(parts) < 2 {
		return "UNKNOWN"
	}
	ext := "." + strings.ToLower(parts[len(parts)-1])
	typeMapping := map[string]string{
		".pdf":  "PDF",
		".doc":  "WORD",
		".docx": "WORD",
		".xls":  "EXCEL",
		".xlsx": "EXCEL",
		".ppt":  "PPT",
		".pptx": "PPT",
		".txt":  "TEXT",
		".md":   "MARKDOWN",
	}
	if typ, ok := typeMapping[ext]; ok {
		return typ
	}
	return strings.ToUpper(ext[1:])
}
