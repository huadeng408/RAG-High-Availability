// Package pipeline 定义了文件处理的核心流程。
package pipeline

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"pai-smart-go/internal/config"
	"pai-smart-go/internal/model"
	"pai-smart-go/internal/repository"
	"pai-smart-go/pkg/database"
	"pai-smart-go/pkg/embedding"
	"pai-smart-go/pkg/es"
	"pai-smart-go/pkg/kafka"
	"pai-smart-go/pkg/log"
	"pai-smart-go/pkg/objectpath"
	"pai-smart-go/pkg/storage"
	"pai-smart-go/pkg/tasks"
	"pai-smart-go/pkg/tika"
	"strconv"
	"time"
	"unicode/utf8"

	"github.com/minio/minio-go/v7"
)

const embeddingCacheTTLSeconds = 7200

// Processor 封装了文件处理阶段的依赖与逻辑。
type Processor struct {
	tikaClient      *tika.Client
	embeddingClient embedding.Client
	esCfg           config.ElasticsearchConfig
	minioCfg        config.MinIOConfig
	embeddingCfg    config.EmbeddingConfig
	kafkaCfg        config.KafkaConfig
	uploadRepo      repository.UploadRepository
	docVectorRepo   repository.DocumentVectorRepository
}

func NewProcessor(
	tikaClient *tika.Client,
	embeddingClient embedding.Client,
	esCfg config.ElasticsearchConfig,
	minioCfg config.MinIOConfig,
	embeddingCfg config.EmbeddingConfig,
	kafkaCfg config.KafkaConfig,
	uploadRepo repository.UploadRepository,
	docVectorRepo repository.DocumentVectorRepository,
) *Processor {
	return &Processor{
		tikaClient:      tikaClient,
		embeddingClient: embeddingClient,
		esCfg:           esCfg,
		minioCfg:        minioCfg,
		embeddingCfg:    embeddingCfg,
		kafkaCfg:        kafkaCfg,
		uploadRepo:      uploadRepo,
		docVectorRepo:   docVectorRepo,
	}
}

// Process routes tasks to specific pipeline stages.
func (p *Processor) Process(ctx context.Context, task tasks.FileProcessingTask) error {
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

func (p *Processor) processParse(ctx context.Context, task tasks.FileProcessingTask) error {
	log.Infof("[Processor][parse] 开始处理 file=%s name=%s", task.FileMD5, task.FileName)
	objectName := objectpath.MergedObjectName(task.FileMD5, task.FileName)
	object, err := storage.MinioClient.GetObject(ctx, p.minioCfg.BucketName, objectName, minio.GetObjectOptions{})
	if err != nil {
		return fmt.Errorf("parse: 从 MinIO 下载文件失败: %w", err)
	}
	defer object.Close()

	buf := new(bytes.Buffer)
	size, err := buf.ReadFrom(object)
	if err != nil {
		return fmt.Errorf("parse: 读取文件流失败: %w", err)
	}
	if size == 0 {
		return errors.New("parse: 文件内容为空")
	}

	textContent, err := p.tikaClient.ExtractText(bytes.NewReader(buf.Bytes()), task.FileName)
	if err != nil {
		return fmt.Errorf("parse: 使用 Tika 提取文本失败: %w", err)
	}
	if textContent == "" {
		return errors.New("parse: 提取的文本内容为空")
	}

	parsedObject := p.parsedObjectName(task.FileMD5)
	reader := bytes.NewReader([]byte(textContent))
	if _, err := storage.MinioClient.PutObject(ctx, p.minioCfg.BucketName, parsedObject, reader, reader.Size(), minio.PutObjectOptions{
		ContentType: "text/plain; charset=utf-8",
	}); err != nil {
		return fmt.Errorf("parse: 持久化解析文本失败: %w", err)
	}

	next := task
	next.Stage = tasks.StageChunk
	next.ParsedObject = parsedObject
	if err := kafka.ProduceTask(next); err != nil {
		return fmt.Errorf("parse: 投递 chunk 阶段消息失败: %w", err)
	}
	log.Infof("[Processor][parse] 完成 file=%s text_len=%d", task.FileMD5, utf8.RuneCountInString(textContent))
	return nil
}

func (p *Processor) processChunk(ctx context.Context, task tasks.FileProcessingTask) error {
	log.Infof("[Processor][chunk] 开始 file=%s", task.FileMD5)
	parsedObject := task.ParsedObject
	if parsedObject == "" {
		parsedObject = p.parsedObjectName(task.FileMD5)
	}
	object, err := storage.MinioClient.GetObject(ctx, p.minioCfg.BucketName, parsedObject, minio.GetObjectOptions{})
	if err != nil {
		return fmt.Errorf("chunk: 读取解析文本失败: %w", err)
	}
	defer object.Close()
	textBytes, err := io.ReadAll(object)
	if err != nil {
		return fmt.Errorf("chunk: 读取解析文本流失败: %w", err)
	}
	textContent := string(textBytes)
	if textContent == "" {
		return errors.New("chunk: 解析文本为空")
	}

	chunks := p.splitText(textContent, 1000, 100)
	if len(chunks) == 0 {
		return errors.New("chunk: 未生成任何文本分块")
	}

	if err := p.docVectorRepo.DeleteByFileMD5(task.FileMD5); err != nil {
		log.Warnf("[Processor][chunk] 清理旧分块失败 file=%s err=%v", task.FileMD5, err)
	}
	dbVectors := make([]*model.DocumentVector, 0, len(chunks))
	for i, chunk := range chunks {
		dbVectors = append(dbVectors, &model.DocumentVector{
			FileMD5:      task.FileMD5,
			ChunkID:      i,
			TextContent:  chunk,
			ModelVersion: p.embeddingCfg.Model,
			UserID:       task.UserID,
			OrgTag:       task.OrgTag,
			IsPublic:     task.IsPublic,
		})
	}
	if err := p.docVectorRepo.BatchCreate(dbVectors); err != nil {
		return fmt.Errorf("chunk: 批量保存文本分块失败: %w", err)
	}

	next := task
	next.Stage = tasks.StageEmbed
	next.ParsedObject = parsedObject
	if err := kafka.ProduceTask(next); err != nil {
		return fmt.Errorf("chunk: 投递 embed 阶段消息失败: %w", err)
	}
	log.Infof("[Processor][chunk] 完成 file=%s chunks=%d", task.FileMD5, len(chunks))
	return nil
}

type cachedEmbedding struct {
	ChunkID int       `json:"chunkId"`
	Vector  []float32 `json:"vector"`
}

func (p *Processor) processEmbed(ctx context.Context, task tasks.FileProcessingTask) error {
	log.Infof("[Processor][embed] 开始 file=%s", task.FileMD5)
	savedVectors, err := p.docVectorRepo.FindByFileMD5(task.FileMD5)
	if err != nil {
		return fmt.Errorf("embed: 读取分块失败: %w", err)
	}
	if len(savedVectors) == 0 {
		return errors.New("embed: 分块为空")
	}

	batchSize := p.kafkaCfg.EmbeddingBatchSize
	if batchSize <= 0 {
		batchSize = 8
	}
	cache := make([]cachedEmbedding, 0, len(savedVectors))
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
			return fmt.Errorf("embed: 批量向量化失败 batch_start=%d: %w", i, err)
		}
		if len(vectors) != len(texts) {
			return fmt.Errorf("embed: 向量数量不匹配 expected=%d actual=%d", len(texts), len(vectors))
		}
		for j := range vectors {
			cache = append(cache, cachedEmbedding{
				ChunkID: savedVectors[i+j].ChunkID,
				Vector:  vectors[j],
			})
		}
	}

	cacheBytes, err := json.Marshal(cache)
	if err != nil {
		return fmt.Errorf("embed: 序列化向量缓存失败: %w", err)
	}
	cacheKey := p.embeddingCacheKey(task.FileMD5)
	if err := database.RDB.Set(ctx, cacheKey, cacheBytes, embeddingCacheTTLSeconds*time.Second).Err(); err != nil {
		return fmt.Errorf("embed: 写入 Redis 向量缓存失败: %w", err)
	}

	next := task
	next.Stage = tasks.StageIndex
	if err := kafka.ProduceTask(next); err != nil {
		return fmt.Errorf("embed: 投递 index 阶段消息失败: %w", err)
	}
	log.Infof("[Processor][embed] 完成 file=%s vectors=%d", task.FileMD5, len(cache))
	return nil
}

func (p *Processor) processIndex(ctx context.Context, task tasks.FileProcessingTask) error {
	log.Infof("[Processor][index] 开始 file=%s", task.FileMD5)
	savedVectors, err := p.docVectorRepo.FindByFileMD5(task.FileMD5)
	if err != nil {
		return fmt.Errorf("index: 读取分块失败: %w", err)
	}
	if len(savedVectors) == 0 {
		return errors.New("index: 分块为空")
	}

	cacheKey := p.embeddingCacheKey(task.FileMD5)
	cacheBytes, err := database.RDB.Get(ctx, cacheKey).Bytes()
	if err != nil {
		return fmt.Errorf("index: 读取 Redis 向量缓存失败: %w", err)
	}
	var cache []cachedEmbedding
	if err := json.Unmarshal(cacheBytes, &cache); err != nil {
		return fmt.Errorf("index: 解析 Redis 向量缓存失败: %w", err)
	}
	vectorMap := make(map[int][]float32, len(cache))
	for _, item := range cache {
		vectorMap[item.ChunkID] = item.Vector
	}

	docs := make([]model.EsDocument, 0, len(savedVectors))
	for _, item := range savedVectors {
		vector, ok := vectorMap[item.ChunkID]
		if !ok || len(vector) == 0 {
			return fmt.Errorf("index: 缺少 chunk=%d 的向量数据", item.ChunkID)
		}
		docs = append(docs, model.EsDocument{
			VectorID:     task.FileMD5 + "_" + strconv.Itoa(item.ChunkID),
			FileMD5:      item.FileMD5,
			ChunkID:      item.ChunkID,
			TextContent:  item.TextContent,
			Vector:       vector,
			ModelVersion: p.embeddingCfg.Model,
			UserID:       item.UserID,
			OrgTag:       item.OrgTag,
			IsPublic:     item.IsPublic,
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
			return fmt.Errorf("index: ES bulk 索引失败 batch_start=%d: %w", i, err)
		}
	}

	_ = database.RDB.Del(ctx, cacheKey).Err()
	_ = storage.MinioClient.RemoveObject(ctx, p.minioCfg.BucketName, p.parsedObjectName(task.FileMD5), minio.RemoveObjectOptions{})
	log.Infof("[Processor][index] 完成 file=%s docs=%d", task.FileMD5, len(docs))
	return nil
}

func (p *Processor) embeddingCacheKey(fileMD5 string) string {
	return "pipeline:embeddings:" + fileMD5
}

func (p *Processor) parsedObjectName(fileMD5 string) string {
	return "parsed/" + fileMD5 + ".txt"
}

// splitText 将长文本按指定大小和重叠进行切分。
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
