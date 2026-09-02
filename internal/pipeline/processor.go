// Package pipeline contains the async document pipeline.
package pipeline

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"strconv"
	"strings"
	"time"
	"unicode/utf8"

	"github.com/huadeng408/RAG-High-Availability/internal/config"
	"github.com/huadeng408/RAG-High-Availability/internal/model"
	"github.com/huadeng408/RAG-High-Availability/internal/repository"
	"github.com/huadeng408/RAG-High-Availability/pkg/database"
	"github.com/huadeng408/RAG-High-Availability/pkg/embedding"
	"github.com/huadeng408/RAG-High-Availability/pkg/es"
	"github.com/huadeng408/RAG-High-Availability/pkg/kafka"
	"github.com/huadeng408/RAG-High-Availability/pkg/log"
	"github.com/huadeng408/RAG-High-Availability/pkg/objectpath"
	"github.com/huadeng408/RAG-High-Availability/pkg/observability"
	orchestratorclient "github.com/huadeng408/RAG-High-Availability/pkg/orchestrator"
	"github.com/huadeng408/RAG-High-Availability/pkg/storage"
	"github.com/huadeng408/RAG-High-Availability/pkg/tasks"
	"github.com/huadeng408/RAG-High-Availability/pkg/tika"

	"github.com/minio/minio-go/v7"
)

const (
	embeddingCacheTTLSeconds   = 7200
	minimumEmbedWindowChunks   = 256
	embedWindowBatchMultiplier = 64
)

// Processor represents a processor.
type Processor struct {
	tikaClient          *tika.Client
	embeddingClient     embedding.Client
	esCfg               config.ElasticsearchConfig
	minioCfg            config.MinIOConfig
	embeddingCfg        config.EmbeddingConfig
	kafkaCfg            config.KafkaConfig
	uploadRepo          repository.UploadRepository
	docVectorRepo       repository.DocumentVectorRepository
	documentVersionRepo repository.DocumentVersionRepository
	evidenceRepo        repository.EvidenceRepository
	ingestionClient     orchestratorclient.IngestionClient
}

// NewProcessor creates a processor.
func NewProcessor(
	tikaClient *tika.Client,
	embeddingClient embedding.Client,
	esCfg config.ElasticsearchConfig,
	minioCfg config.MinIOConfig,
	embeddingCfg config.EmbeddingConfig,
	kafkaCfg config.KafkaConfig,
	uploadRepo repository.UploadRepository,
	docVectorRepo repository.DocumentVectorRepository,
	documentVersionRepo repository.DocumentVersionRepository,
	evidenceRepo repository.EvidenceRepository,
	ingestionClient orchestratorclient.IngestionClient,
) *Processor {
	return &Processor{
		tikaClient:          tikaClient,
		embeddingClient:     embeddingClient,
		esCfg:               esCfg,
		minioCfg:            minioCfg,
		embeddingCfg:        embeddingCfg,
		kafkaCfg:            kafkaCfg,
		uploadRepo:          uploadRepo,
		docVectorRepo:       docVectorRepo,
		documentVersionRepo: documentVersionRepo,
		evidenceRepo:        evidenceRepo,
		ingestionClient:     ingestionClient,
	}
}

// Process handles process.
func (p *Processor) Process(ctx context.Context, task tasks.FileProcessingTask) error {
	if task.TraceID != "" {
		ctx = observability.WithTraceID(ctx, task.TraceID)
	}
	ctx, span := observability.StartSpan(ctx, "pipeline."+string(task.Stage))
	defer span.End()
	span.SetAttribute("document_version", taskIdentity(task))
	switch task.Stage {
	case tasks.StageParse:
		return p.processParse(ctx, task)
	case tasks.StageChunk:
		return p.processChunk(ctx, task)
	case tasks.StageEmbed:
		return p.processEmbed(ctx, task)
	case tasks.StageIndex:
		return p.processIndex(ctx, task)
	default:
		return fmt.Errorf("unknown pipeline stage: %s", task.Stage)
	}
}

// processParse processes parse.
func (p *Processor) processParse(ctx context.Context, task tasks.FileProcessingTask) error {
	log.Infof("[Processor][parse] start file=%s name=%s", task.FileMD5, task.FileName)

	if p.ingestionClient != nil && p.ingestionClient.Enabled() {
		return p.processParseExternal(ctx, task)
	}

	objectName := objectpath.MergedObjectName(task.FileMD5, task.FileName)
	object, err := storage.MinioClient.GetObject(ctx, p.minioCfg.BucketName, objectName, minio.GetObjectOptions{})
	if err != nil {
		return fmt.Errorf("parse: download object failed: %w", err)
	}
	defer object.Close()

	buf := new(bytes.Buffer)
	size, err := buf.ReadFrom(object)
	if err != nil {
		return fmt.Errorf("parse: read object stream failed: %w", err)
	}
	if size == 0 {
		return errors.New("parse: empty file content")
	}
	if task.DocumentVersion == "" {
		task, err = p.ensureTaskVersion(ctx, task, buf.Bytes())
		if err != nil {
			return fmt.Errorf("parse: create document version failed: %w", err)
		}
	}

	textContent, err := p.tikaClient.ExtractText(bytes.NewReader(buf.Bytes()), task.FileName)
	if err != nil {
		return fmt.Errorf("parse: tika extract failed: %w", err)
	}
	if textContent == "" {
		return errors.New("parse: extracted text is empty")
	}

	parsedObject := p.parsedObjectName(task.FileMD5)
	reader := bytes.NewReader([]byte(textContent))
	if _, err := storage.MinioClient.PutObject(
		ctx,
		p.minioCfg.BucketName,
		parsedObject,
		reader,
		reader.Size(),
		minio.PutObjectOptions{ContentType: "text/plain; charset=utf-8"},
	); err != nil {
		return fmt.Errorf("parse: persist parsed text failed: %w", err)
	}

	next := task
	next.Stage = tasks.StageChunk
	next.ParsedObject = parsedObject
	if err := kafka.ProduceTask(next); err != nil {
		return fmt.Errorf("parse: enqueue chunk task failed: %w", err)
	}
	log.Infof("[Processor][parse] done file=%s text_len=%d", task.FileMD5, utf8.RuneCountInString(textContent))
	return nil
}

// processChunk processes chunk.
func (p *Processor) processChunk(ctx context.Context, task tasks.FileProcessingTask) error {
	log.Infof("[Processor][chunk] start file=%s", task.FileMD5)

	if p.ingestionClient != nil && p.ingestionClient.Enabled() {
		return p.processChunkExternal(ctx, task)
	}

	parsedObject := task.ParsedObject
	if parsedObject == "" {
		parsedObject = p.parsedObjectName(task.FileMD5)
	}

	object, err := storage.MinioClient.GetObject(ctx, p.minioCfg.BucketName, parsedObject, minio.GetObjectOptions{})
	if err != nil {
		return fmt.Errorf("chunk: read parsed object failed: %w", err)
	}
	defer object.Close()

	textBytes, err := io.ReadAll(object)
	if err != nil {
		return fmt.Errorf("chunk: read parsed stream failed: %w", err)
	}
	textContent := string(textBytes)
	if textContent == "" {
		return errors.New("chunk: parsed text is empty")
	}

	chunks := p.splitText(textContent, 1000, 100)
	if len(chunks) == 0 {
		return errors.New("chunk: no chunks generated")
	}

	if err := p.deleteVectors(task); err != nil {
		log.Warnf("[Processor][chunk] clear old chunks identity=%s err=%v", taskIdentity(task), err)
	}
	_ = database.RDB.Del(ctx, p.embeddingCacheKey(taskIdentity(task))).Err()

	dbVectors := make([]*model.DocumentVector, 0, len(chunks))
	for i, chunk := range chunks {
		dbVectors = append(dbVectors, &model.DocumentVector{
			FileMD5:         task.FileMD5,
			DocumentVersion: taskIdentity(task),
			ChunkID:         i,
			TextContent:     chunk,
			ModelVersion:    p.embeddingCfg.Model,
			UserID:          task.UserID,
			OrgTag:          task.OrgTag,
			IsPublic:        task.IsPublic,
		})
	}
	if err := p.docVectorRepo.BatchCreate(dbVectors); err != nil {
		return fmt.Errorf("chunk: persist chunks failed: %w", err)
	}

	next := task
	next.Stage = tasks.StageEmbed
	next.ParsedObject = parsedObject
	next.TaskChunkID = 1
	next.ChunkStart = 0
	next.TotalChunks = len(chunks)
	if err := kafka.ProduceTask(next); err != nil {
		return fmt.Errorf("chunk: enqueue embed task failed: %w", err)
	}
	log.Infof("[Processor][chunk] done file=%s chunks=%d", task.FileMD5, len(chunks))
	return nil
}

// cachedEmbedding represents a cached embedding.
type cachedEmbedding struct {
	ChunkID int       `json:"chunkId"`
	Vector  []float32 `json:"vector"`
}

// processEmbed processes embed.
func (p *Processor) processEmbed(ctx context.Context, task tasks.FileProcessingTask) error {
	if p.ingestionClient != nil && p.ingestionClient.Enabled() {
		return p.processEmbedExternal(ctx, task)
	}

	cacheKey := p.embeddingCacheKey(taskIdentity(task))
	if err := p.ensureEmbeddingHashCache(ctx, cacheKey); err != nil {
		return fmt.Errorf("embed: prepare cache failed: %w", err)
	}

	totalChunks := task.TotalChunks
	if totalChunks <= 0 {
		count, err := p.countVectors(task)
		if err != nil {
			return fmt.Errorf("embed: count chunks failed: %w", err)
		}
		totalChunks = int(count)
	}
	if totalChunks == 0 {
		return errors.New("embed: chunks are empty")
	}

	windowSize := p.embedWindowChunks()
	chunkStart := task.ChunkStart
	if chunkStart < 0 {
		chunkStart = 0
	}
	if chunkStart >= totalChunks {
		return p.enqueueIndexTask(task, totalChunks)
	}

	limit := windowSize
	remaining := totalChunks - chunkStart
	if remaining < limit {
		limit = remaining
	}
	savedVectors, err := p.findVectorRange(task, chunkStart, limit)
	if err != nil {
		return fmt.Errorf("embed: load chunk range failed start=%d limit=%d: %w", chunkStart, limit, err)
	}
	if len(savedVectors) == 0 {
		return fmt.Errorf("embed: no chunks found in range start=%d limit=%d", chunkStart, limit)
	}

	batchSize := p.kafkaCfg.EmbeddingBatchSize
	if batchSize <= 0 {
		batchSize = 8
	}

	log.Infof(
		"[Processor][embed] start file=%s task_chunk=%d start=%d window=%d total=%d",
		task.FileMD5,
		task.TaskChunkID,
		chunkStart,
		len(savedVectors),
		totalChunks,
	)

	for i := 0; i < len(savedVectors); i += batchSize {
		end := i + batchSize
		if end > len(savedVectors) {
			end = len(savedVectors)
		}

		texts := make([]string, 0, end-i)
		for _, item := range savedVectors[i:end] {
			texts = append(texts, item.TextContent)
		}
		vectors, err := p.embeddingClient.CreateEmbeddings(ctx, texts)
		if err != nil {
			return fmt.Errorf("embed: embedding batch failed batch_start=%d: %w", i, err)
		}
		if len(vectors) != len(texts) {
			return fmt.Errorf("embed: vector count mismatch expected=%d actual=%d", len(texts), len(vectors))
		}

		kv := make(map[string]interface{}, len(vectors))
		for j := range vectors {
			vectorBytes, err := json.Marshal(vectors[j])
			if err != nil {
				return fmt.Errorf("embed: marshal vector failed chunk=%d: %w", savedVectors[i+j].ChunkID, err)
			}
			kv[strconv.Itoa(savedVectors[i+j].ChunkID)] = string(vectorBytes)
		}
		if len(kv) > 0 {
			if err := database.RDB.HSet(ctx, cacheKey, kv).Err(); err != nil {
				return fmt.Errorf("embed: write vector cache failed: %w", err)
			}
		}
	}
	if err := database.RDB.Expire(ctx, cacheKey, embeddingCacheTTLSeconds*time.Second).Err(); err != nil {
		return fmt.Errorf("embed: refresh cache ttl failed: %w", err)
	}

	nextStart := chunkStart + len(savedVectors)
	if nextStart < totalChunks {
		taskChunkID := task.TaskChunkID
		if taskChunkID <= 0 {
			taskChunkID = chunkStart/windowSize + 1
		}
		next := task
		next.Stage = tasks.StageEmbed
		next.TaskChunkID = taskChunkID + 1
		next.ChunkStart = nextStart
		next.TotalChunks = totalChunks
		if err := kafka.ProduceTask(next); err != nil {
			return fmt.Errorf("embed: enqueue next embed task failed: %w", err)
		}
		log.Infof(
			"[Processor][embed] partial file=%s done=%d/%d next_start=%d next_task_chunk=%d",
			task.FileMD5,
			nextStart,
			totalChunks,
			next.ChunkStart,
			next.TaskChunkID,
		)
		return nil
	}

	if err := p.enqueueIndexTask(task, totalChunks); err != nil {
		return err
	}
	log.Infof("[Processor][embed] done file=%s total=%d", task.FileMD5, totalChunks)
	return nil
}

// processIndex processes index.
func (p *Processor) processIndex(ctx context.Context, task tasks.FileProcessingTask) error {
	log.Infof("[Processor][index] start file=%s", task.FileMD5)

	if p.ingestionClient != nil && p.ingestionClient.Enabled() {
		return p.processIndexExternal(ctx, task)
	}

	savedVectors, err := p.findVectors(task)
	if err != nil {
		return fmt.Errorf("index: load chunks failed: %w", err)
	}
	if len(savedVectors) == 0 {
		return errors.New("index: chunks are empty")
	}

	cacheKey := p.embeddingCacheKey(taskIdentity(task))
	vectorMap, err := p.loadCachedEmbeddingMap(ctx, cacheKey)
	if err != nil {
		return fmt.Errorf("index: read cached vectors failed: %w", err)
	}
	if len(vectorMap) == 0 {
		return errors.New("index: cached vectors are empty")
	}

	docs := make([]model.EsDocument, 0, len(savedVectors))
	for _, item := range savedVectors {
		vector, ok := vectorMap[item.ChunkID]
		if !ok || len(vector) == 0 {
			return fmt.Errorf("index: missing vector for chunk=%d", item.ChunkID)
		}
		docs = append(docs, model.EsDocument{
			VectorID:        taskIdentity(task) + "_" + strconv.Itoa(item.ChunkID),
			FileMD5:         item.FileMD5,
			DocumentVersion: item.DocumentVersion,
			ChunkID:         item.ChunkID,
			TextContent:     item.TextContent,
			Vector:          vector,
			ModelVersion:    p.embeddingCfg.Model,
			UserID:          item.UserID,
			OrgTag:          item.OrgTag,
			IsPublic:        item.IsPublic,
			Modality:        item.Modality,
			Page:            item.Page,
			Slide:           item.Slide,
			Sheet:           item.Sheet,
			EvidenceIDs:     item.EvidenceIDs,
			BBox:            item.BBox,
			Image:           item.Image,
		})
	}

	bulkSize := p.kafkaCfg.ESBulkBatchSize
	if bulkSize <= 0 {
		bulkSize = 100
	}
	for i := 0; i < len(docs); i += bulkSize {
		end := i + bulkSize
		if end > len(docs) {
			end = len(docs)
		}
		if err := es.BulkIndexDocuments(ctx, p.esCfg.IndexName, docs[i:end]); err != nil {
			return fmt.Errorf("index: bulk index failed batch_start=%d: %w", i, err)
		}
	}
	if err := p.indexEvidence(ctx, task); err != nil {
		return err
	}

	_ = database.RDB.Del(ctx, cacheKey).Err()
	_ = storage.MinioClient.RemoveObject(ctx, p.minioCfg.BucketName, p.parsedObjectName(taskIdentity(task)), minio.RemoveObjectOptions{})
	log.Infof("[Processor][index] done file=%s docs=%d", task.FileMD5, len(docs))
	return nil
}

// processParseExternal delegates parse-stage execution to the external ingestion worker.
func (p *Processor) processParseExternal(ctx context.Context, task tasks.FileProcessingTask) error {
	var err error
	task, err = p.ensureTaskVersion(ctx, task, nil)
	if err != nil {
		return fmt.Errorf("parse: create document version failed: %w", err)
	}
	objectURL := task.ObjectURL
	if strings.TrimSpace(objectURL) == "" {
		objectName := objectpath.MergedObjectName(task.FileMD5, task.FileName)
		url, err := storage.GetPresignedURL(p.minioCfg.BucketName, objectName, time.Hour)
		if err != nil {
			return fmt.Errorf("parse: generate presigned url failed: %w", err)
		}
		objectURL = url
	}

	parsedDocument, err := p.ingestionClient.Parse(ctx, task, objectURL)
	if err != nil {
		return fmt.Errorf("parse: external worker failed: %w", err)
	}
	if len(parsedDocument.Chunks) == 0 {
		return errors.New("parse: structured document has no chunks")
	}
	if parsedDocument.DocumentVersion != task.DocumentVersion {
		return fmt.Errorf("parse: worker returned document version %q, expected %q", parsedDocument.DocumentVersion, task.DocumentVersion)
	}

	parsedObject := p.parsedObjectName(task.DocumentVersion)
	parsedBytes, err := json.Marshal(parsedDocument)
	if err != nil {
		return fmt.Errorf("parse: encode structured artifact failed: %w", err)
	}
	reader := bytes.NewReader(parsedBytes)
	if _, err := storage.MinioClient.PutObject(
		ctx,
		p.minioCfg.BucketName,
		parsedObject,
		reader,
		reader.Size(),
		minio.PutObjectOptions{ContentType: "application/json"},
	); err != nil {
		return fmt.Errorf("parse: persist parsed text failed: %w", err)
	}

	next := task
	next.Stage = tasks.StageChunk
	next.ParsedObject = parsedObject
	if err := kafka.ProduceTask(next); err != nil {
		return fmt.Errorf("parse: enqueue chunk task failed: %w", err)
	}
	log.Infof("[Processor][parse] done file=%s chunks=%d worker=external", task.FileMD5, len(parsedDocument.Chunks))
	return nil
}

// processChunkExternal delegates chunk-stage execution to the external ingestion worker.
func (p *Processor) processChunkExternal(ctx context.Context, task tasks.FileProcessingTask) error {
	log.Infof("[Processor][chunk] external worker file=%s", task.FileMD5)

	parsedObject := task.ParsedObject
	if parsedObject == "" {
		parsedObject = p.parsedObjectName(task.DocumentVersion)
	}

	object, err := storage.MinioClient.GetObject(ctx, p.minioCfg.BucketName, parsedObject, minio.GetObjectOptions{})
	if err != nil {
		return fmt.Errorf("chunk: read parsed object failed: %w", err)
	}
	defer object.Close()

	textBytes, err := io.ReadAll(object)
	if err != nil {
		return fmt.Errorf("chunk: read parsed stream failed: %w", err)
	}
	var parsedDocument model.ParsedDocument
	if err := json.Unmarshal(textBytes, &parsedDocument); err != nil {
		return fmt.Errorf("chunk: decode structured artifact failed: %w", err)
	}

	chunks, err := p.ingestionClient.Chunk(ctx, task, parsedDocument, 1000, 100)
	if err != nil {
		return fmt.Errorf("chunk: external worker failed: %w", err)
	}
	if len(chunks) == 0 {
		return errors.New("chunk: no chunks generated")
	}
	if parsedDocument.DocumentVersion != task.DocumentVersion {
		return fmt.Errorf("chunk: parsed document version %q does not match task version %q", parsedDocument.DocumentVersion, task.DocumentVersion)
	}
	if p.evidenceRepo == nil {
		return errors.New("chunk: evidence repository is not configured")
	}
	if err := p.evidenceRepo.ReplaceForVersion(task.DocumentVersion, parsedDocument.EvidenceUnits); err != nil {
		return fmt.Errorf("chunk: persist evidence failed: %w", err)
	}

	dbVectors, err := buildStructuredVectors(task, parsedDocument, chunks, p.embeddingCfg.Model)
	if err != nil {
		return fmt.Errorf("chunk: build versioned vectors failed: %w", err)
	}
	if err := p.docVectorRepo.DeleteByDocumentVersion(task.DocumentVersion); err != nil {
		log.Warnf("[Processor][chunk] clear old chunks version=%s err=%v", task.DocumentVersion, err)
	}
	_ = database.RDB.Del(ctx, p.embeddingCacheKey(task.DocumentVersion)).Err()
	if err := p.docVectorRepo.BatchCreate(dbVectors); err != nil {
		return fmt.Errorf("chunk: persist chunks failed: %w", err)
	}

	next := task
	next.Stage = tasks.StageEmbed
	next.ParsedObject = parsedObject
	next.TaskChunkID = 1
	next.ChunkStart = 0
	next.TotalChunks = len(chunks)
	if err := kafka.ProduceTask(next); err != nil {
		return fmt.Errorf("chunk: enqueue embed task failed: %w", err)
	}
	log.Infof("[Processor][chunk] done file=%s chunks=%d worker=external", task.FileMD5, len(chunks))
	return nil
}

// processEmbedExternal delegates embedding-stage execution to the external ingestion worker.
func (p *Processor) processEmbedExternal(ctx context.Context, task tasks.FileProcessingTask) error {
	if task.DocumentVersion == "" {
		return errors.New("embed: document version is required")
	}
	cacheKey := p.embeddingCacheKey(task.DocumentVersion)
	if err := p.ensureEmbeddingHashCache(ctx, cacheKey); err != nil {
		return fmt.Errorf("embed: prepare cache failed: %w", err)
	}

	totalChunks := task.TotalChunks
	if totalChunks <= 0 {
		count, err := p.docVectorRepo.CountByDocumentVersion(task.DocumentVersion)
		if err != nil {
			return fmt.Errorf("embed: count chunks failed: %w", err)
		}
		totalChunks = int(count)
	}
	if totalChunks == 0 {
		return errors.New("embed: chunks are empty")
	}

	windowSize := p.embedWindowChunks()
	chunkStart := task.ChunkStart
	if chunkStart < 0 {
		chunkStart = 0
	}
	if chunkStart >= totalChunks {
		return p.enqueueIndexTask(task, totalChunks)
	}

	limit := windowSize
	remaining := totalChunks - chunkStart
	if remaining < limit {
		limit = remaining
	}
	savedVectors, err := p.docVectorRepo.FindByDocumentVersionRange(task.DocumentVersion, chunkStart, limit)
	if err != nil {
		return fmt.Errorf("embed: load chunk range failed start=%d limit=%d: %w", chunkStart, limit, err)
	}
	if len(savedVectors) == 0 {
		return fmt.Errorf("embed: no chunks found in range start=%d limit=%d", chunkStart, limit)
	}

	batchSize := p.kafkaCfg.EmbeddingBatchSize
	if batchSize <= 0 {
		batchSize = 8
	}

	log.Infof(
		"[Processor][embed] start file=%s task_chunk=%d start=%d window=%d total=%d worker=external",
		task.FileMD5,
		task.TaskChunkID,
		chunkStart,
		len(savedVectors),
		totalChunks,
	)

	for i := 0; i < len(savedVectors); i += batchSize {
		end := i + batchSize
		if end > len(savedVectors) {
			end = len(savedVectors)
		}

		texts := make([]string, 0, end-i)
		for _, item := range savedVectors[i:end] {
			texts = append(texts, item.TextContent)
		}
		vectors, err := p.ingestionClient.Embed(ctx, task, texts)
		if err != nil {
			return fmt.Errorf("embed: external worker failed batch_start=%d: %w", i, err)
		}
		if len(vectors) != len(texts) {
			return fmt.Errorf("embed: vector count mismatch expected=%d actual=%d", len(texts), len(vectors))
		}

		kv := make(map[string]interface{}, len(vectors))
		for j := range vectors {
			vectorBytes, err := json.Marshal(vectors[j])
			if err != nil {
				return fmt.Errorf("embed: marshal vector failed chunk=%d: %w", savedVectors[i+j].ChunkID, err)
			}
			kv[strconv.Itoa(savedVectors[i+j].ChunkID)] = string(vectorBytes)
		}
		if len(kv) > 0 {
			if err := database.RDB.HSet(ctx, cacheKey, kv).Err(); err != nil {
				return fmt.Errorf("embed: write vector cache failed: %w", err)
			}
		}
	}
	if err := database.RDB.Expire(ctx, cacheKey, embeddingCacheTTLSeconds*time.Second).Err(); err != nil {
		return fmt.Errorf("embed: refresh cache ttl failed: %w", err)
	}

	nextStart := chunkStart + len(savedVectors)
	if nextStart < totalChunks {
		taskChunkID := task.TaskChunkID
		if taskChunkID <= 0 {
			taskChunkID = chunkStart/windowSize + 1
		}
		next := task
		next.Stage = tasks.StageEmbed
		next.TaskChunkID = taskChunkID + 1
		next.ChunkStart = nextStart
		next.TotalChunks = totalChunks
		if err := kafka.ProduceTask(next); err != nil {
			return fmt.Errorf("embed: enqueue next embed task failed: %w", err)
		}
		log.Infof(
			"[Processor][embed] partial file=%s done=%d/%d next_start=%d next_task_chunk=%d worker=external",
			task.FileMD5,
			nextStart,
			totalChunks,
			next.ChunkStart,
			next.TaskChunkID,
		)
		return nil
	}

	if err := p.enqueueIndexTask(task, totalChunks); err != nil {
		return err
	}
	log.Infof("[Processor][embed] done file=%s total=%d worker=external", task.FileMD5, totalChunks)
	return nil
}

// processIndexExternal delegates index-stage execution to the external ingestion worker.
func (p *Processor) processIndexExternal(ctx context.Context, task tasks.FileProcessingTask) error {
	log.Infof("[Processor][index] start file=%s worker=external", task.FileMD5)
	if task.DocumentVersion == "" {
		return errors.New("index: document version is required")
	}

	savedVectors, err := p.docVectorRepo.FindByDocumentVersion(task.DocumentVersion)
	if err != nil {
		return fmt.Errorf("index: load chunks failed: %w", err)
	}
	if len(savedVectors) == 0 {
		return errors.New("index: chunks are empty")
	}

	cacheKey := p.embeddingCacheKey(task.DocumentVersion)
	vectorMap, err := p.loadCachedEmbeddingMap(ctx, cacheKey)
	if err != nil {
		return fmt.Errorf("index: read cached vectors failed: %w", err)
	}
	if len(vectorMap) == 0 {
		return errors.New("index: cached vectors are empty")
	}

	docs := make([]model.EsDocument, 0, len(savedVectors))
	for _, item := range savedVectors {
		vector, ok := vectorMap[item.ChunkID]
		if !ok || len(vector) == 0 {
			return fmt.Errorf("index: missing vector for chunk=%d", item.ChunkID)
		}
		docs = append(docs, model.EsDocument{
			VectorID:        task.DocumentVersion + "_" + strconv.Itoa(item.ChunkID),
			FileMD5:         item.FileMD5,
			DocumentVersion: item.DocumentVersion,
			ChunkID:         item.ChunkID,
			TextContent:     item.TextContent,
			Vector:          vector,
			ModelVersion:    p.embeddingCfg.Model,
			UserID:          item.UserID,
			OrgTag:          item.OrgTag,
			IsPublic:        item.IsPublic,
			Modality:        item.Modality,
			Page:            item.Page,
			Slide:           item.Slide,
			Sheet:           item.Sheet,
			EvidenceIDs:     item.EvidenceIDs,
			BBox:            item.BBox,
			Image:           item.Image,
		})
	}

	if _, err := p.ingestionClient.Index(ctx, task, p.esCfg.IndexName, docs); err != nil {
		return fmt.Errorf("index: external worker failed: %w", err)
	}
	if err := p.indexEvidence(ctx, task); err != nil {
		return err
	}

	_ = database.RDB.Del(ctx, cacheKey).Err()
	_ = storage.MinioClient.RemoveObject(ctx, p.minioCfg.BucketName, p.parsedObjectName(task.DocumentVersion), minio.RemoveObjectOptions{})
	log.Infof("[Processor][index] done file=%s docs=%d worker=external", task.FileMD5, len(docs))
	return nil
}

// indexEvidence writes source-level evidence after knowledge chunks commit.
// Stable evidence IDs make replay idempotent in Elasticsearch.
func (p *Processor) indexEvidence(ctx context.Context, task tasks.FileProcessingTask) error {
	if p.evidenceRepo == nil {
		return nil
	}
	evidence, err := p.evidenceRepo.ListByVersion(taskIdentity(task))
	if err != nil {
		return fmt.Errorf("index: load evidence failed: %w", err)
	}
	docs := es.BuildEvidenceDocuments(task.FileMD5, task.UserID, task.OrgTag, task.IsPublic, evidence)
	if len(docs) == 0 {
		return nil
	}
	if err := es.BulkIndexEvidenceDocuments(ctx, es.EvidenceReadAlias, docs); err != nil {
		return fmt.Errorf("index: bulk evidence index failed: %w", err)
	}
	return nil
}

// enqueueIndexTask handles enqueue index task.
func (p *Processor) enqueueIndexTask(task tasks.FileProcessingTask, totalChunks int) error {
	next := task
	next.Stage = tasks.StageIndex
	next.TaskChunkID = 0
	next.ChunkStart = 0
	next.TotalChunks = totalChunks
	if err := kafka.ProduceTask(next); err != nil {
		return fmt.Errorf("embed: enqueue index task failed: %w", err)
	}
	return nil
}

// ensureEmbeddingHashCache ensures embedding hash cache.
func (p *Processor) ensureEmbeddingHashCache(ctx context.Context, cacheKey string) error {
	cacheType, err := database.RDB.Type(ctx, cacheKey).Result()
	if err != nil {
		return err
	}
	if cacheType == "none" || cacheType == "hash" {
		return nil
	}
	if err := database.RDB.Del(ctx, cacheKey).Err(); err != nil {
		return err
	}
	return nil
}

// loadCachedEmbeddingMap loads cached embedding map.
func (p *Processor) loadCachedEmbeddingMap(ctx context.Context, cacheKey string) (map[int][]float32, error) {
	cacheType, err := database.RDB.Type(ctx, cacheKey).Result()
	if err != nil {
		return nil, err
	}

	switch cacheType {
	case "hash":
		return p.loadEmbeddingMapFromHash(ctx, cacheKey)
	case "string":
		return p.loadEmbeddingMapFromLegacyString(ctx, cacheKey)
	case "none":
		return nil, errors.New("embedding cache key not found")
	default:
		return nil, fmt.Errorf("unsupported embedding cache type: %s", cacheType)
	}
}

// loadEmbeddingMapFromHash loads embedding map from hash.
func (p *Processor) loadEmbeddingMapFromHash(ctx context.Context, cacheKey string) (map[int][]float32, error) {
	rawMap, err := database.RDB.HGetAll(ctx, cacheKey).Result()
	if err != nil {
		return nil, err
	}
	vectorMap := make(map[int][]float32, len(rawMap))
	for field, value := range rawMap {
		chunkID, err := strconv.Atoi(field)
		if err != nil {
			return nil, fmt.Errorf("invalid chunk id in cache field=%s: %w", field, err)
		}
		var vector []float32
		if err := json.Unmarshal([]byte(value), &vector); err != nil {
			return nil, fmt.Errorf("invalid vector for chunk=%d: %w", chunkID, err)
		}
		vectorMap[chunkID] = vector
	}
	return vectorMap, nil
}

// loadEmbeddingMapFromLegacyString loads embedding map from legacy string.
func (p *Processor) loadEmbeddingMapFromLegacyString(ctx context.Context, cacheKey string) (map[int][]float32, error) {
	cacheBytes, err := database.RDB.Get(ctx, cacheKey).Bytes()
	if err != nil {
		return nil, err
	}

	var cache []cachedEmbedding
	if err := json.Unmarshal(cacheBytes, &cache); err != nil {
		return nil, err
	}
	vectorMap := make(map[int][]float32, len(cache))
	for _, item := range cache {
		vectorMap[item.ChunkID] = item.Vector
	}
	return vectorMap, nil
}

// embeddingCacheKey handles embedding cache key.
func (p *Processor) embeddingCacheKey(fileMD5 string) string {
	return "pipeline:embeddings:" + fileMD5
}

// parsedObjectName handles parsed object name.
func (p *Processor) parsedObjectName(fileMD5 string) string {
	return "parsed/" + fileMD5 + ".json"
}

func taskIdentity(task tasks.FileProcessingTask) string {
	if strings.TrimSpace(task.DocumentVersion) != "" {
		return task.DocumentVersion
	}
	return task.FileMD5
}

func (p *Processor) findVectors(task tasks.FileProcessingTask) ([]*model.DocumentVector, error) {
	if task.DocumentVersion != "" {
		return p.docVectorRepo.FindByDocumentVersion(task.DocumentVersion)
	}
	return p.docVectorRepo.FindByFileMD5(task.FileMD5)
}

func (p *Processor) findVectorRange(task tasks.FileProcessingTask, offset, limit int) ([]*model.DocumentVector, error) {
	if task.DocumentVersion != "" {
		return p.docVectorRepo.FindByDocumentVersionRange(task.DocumentVersion, offset, limit)
	}
	return p.docVectorRepo.FindByFileMD5Range(task.FileMD5, offset, limit)
}

func (p *Processor) countVectors(task tasks.FileProcessingTask) (int64, error) {
	if task.DocumentVersion != "" {
		return p.docVectorRepo.CountByDocumentVersion(task.DocumentVersion)
	}
	return p.docVectorRepo.CountByFileMD5(task.FileMD5)
}

func (p *Processor) deleteVectors(task tasks.FileProcessingTask) error {
	if task.DocumentVersion != "" {
		return p.docVectorRepo.DeleteByDocumentVersion(task.DocumentVersion)
	}
	return p.docVectorRepo.DeleteByFileMD5(task.FileMD5)
}

// ensureTaskVersion creates the immutable version before parse/chunk work starts.
// The checksum remains upload metadata; all derived artifacts use the version ID.
func (p *Processor) ensureTaskVersion(ctx context.Context, task tasks.FileProcessingTask, contents []byte) (tasks.FileProcessingTask, error) {
	if strings.TrimSpace(task.DocumentVersion) != "" {
		return task, nil
	}
	if p.documentVersionRepo == nil {
		return task, errors.New("document version repository is not configured")
	}
	if len(contents) == 0 {
		objectName := objectpath.MergedObjectName(task.FileMD5, task.FileName)
		object, err := storage.MinioClient.GetObject(ctx, p.minioCfg.BucketName, objectName, minio.GetObjectOptions{})
		if err != nil {
			return task, fmt.Errorf("download merged object: %w", err)
		}
		defer object.Close()
		contents, err = io.ReadAll(object)
		if err != nil {
			return task, fmt.Errorf("read merged object: %w", err)
		}
	}
	if len(contents) == 0 {
		return task, errors.New("merged object is empty")
	}
	source := model.DocumentSource{
		SourceID:          "upload:" + task.FileMD5,
		FileName:          task.FileName,
		OwnerID:           strconv.FormatUint(uint64(task.UserID), 10),
		Organization:      task.OrgTag,
		IsPublic:          task.IsPublic,
		OriginalObjectKey: objectpath.MergedObjectName(task.FileMD5, task.FileName),
		FileMD5:           task.FileMD5,
	}
	version, err := p.documentVersionRepo.CreateForUpload(source, contents, "structured-ingestion", "1")
	if err != nil {
		return task, err
	}
	task.DocumentVersion = version.DocumentVersionID
	return task, nil
}

// embedWindowChunks handles embed window chunks.
func (p *Processor) embedWindowChunks() int {
	batchSize := p.kafkaCfg.EmbeddingBatchSize
	if batchSize <= 0 {
		batchSize = 8
	}
	window := batchSize * embedWindowBatchMultiplier
	if window < minimumEmbedWindowChunks {
		window = minimumEmbedWindowChunks
	}
	return window
}

// splitText splits text.
func (p *Processor) splitText(text string, chunkSize int, chunkOverlap int) []string {
	if chunkSize <= chunkOverlap {
		return p.simpleSplit(text, chunkSize)
	}

	var chunks []string
	runes := []rune(text)
	if len(runes) == 0 {
		return nil
	}

	step := chunkSize - chunkOverlap
	for i := 0; i < len(runes); i += step {
		end := i + chunkSize
		if end > len(runes) {
			end = len(runes)
		}
		chunks = append(chunks, string(runes[i:end]))
		if end == len(runes) {
			break
		}
	}
	return chunks
}

// simpleSplit handles simple split.
func (p *Processor) simpleSplit(text string, chunkSize int) []string {
	var chunks []string
	runes := []rune(text)
	if len(runes) == 0 {
		return nil
	}
	for i := 0; i < len(runes); i += chunkSize {
		end := i + chunkSize
		if end > len(runes) {
			end = len(runes)
		}
		chunks = append(chunks, string(runes[i:end]))
	}
	return chunks
}
