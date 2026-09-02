// Package kafka 提供了与 Kafka 消息队列交互的功能。
package kafka

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"github.com/huadeng408/RAG-High-Availability/internal/config"
	"github.com/huadeng408/RAG-High-Availability/internal/model"
	"github.com/huadeng408/RAG-High-Availability/internal/repository"
	"github.com/huadeng408/RAG-High-Availability/pkg/log"
	"github.com/huadeng408/RAG-High-Availability/pkg/tasks"
	"net"
	"strings"
	"time"

	"github.com/segmentio/kafka-go"
)

// TaskProcessor defines the interface for any service that can process a task.
type TaskProcessor interface {
	Process(ctx context.Context, task tasks.FileProcessingTask) error
}

// topicSet represents a topic set.
type topicSet struct {
	parse string
	chunk string
	embed string
	index string
	dlq   string
}

var (
	writers        map[string]*kafka.Writer
	topics         topicSet
	producerCfg    config.KafkaConfig
	producerDialer *kafka.Dialer
)

// normalizeKafkaConfig normalizes kafka config.
func normalizeKafkaConfig(cfg config.KafkaConfig) config.KafkaConfig {
	if cfg.ConsumerGroupPrefix == "" {
		cfg.ConsumerGroupPrefix = "github.com/huadeng408/RAG-High-Availability"
	}
	if cfg.MaxRetries <= 0 {
		cfg.MaxRetries = 3
	}
	if cfg.BaseBackoffMs <= 0 {
		cfg.BaseBackoffMs = 800
	}
	if cfg.EmbeddingBatchSize <= 0 {
		cfg.EmbeddingBatchSize = 8
	}
	if cfg.ESBulkBatchSize <= 0 {
		cfg.ESBulkBatchSize = 100
	}
	if cfg.Topics.Parse == "" {
		if cfg.Topic != "" {
			cfg.Topics.Parse = cfg.Topic
		} else {
			cfg.Topics.Parse = "file-parse"
		}
	}
	if cfg.Topics.Chunk == "" {
		cfg.Topics.Chunk = "file-chunk"
	}
	if cfg.Topics.Embed == "" {
		cfg.Topics.Embed = "file-embed"
	}
	if cfg.Topics.Index == "" {
		cfg.Topics.Index = "file-index"
	}
	if cfg.Topics.DLQ == "" {
		cfg.Topics.DLQ = "file-dlq"
	}
	return cfg
}

// InitProducer initializes writers for all pipeline topics.
func InitProducer(cfg config.KafkaConfig) {
	cfg = normalizeKafkaConfig(cfg)
	producerCfg = cfg
	brokers := parseKafkaBrokers(cfg.Brokers)
	dialer := &kafka.Dialer{
		Timeout:   10 * time.Second,
		DualStack: true,
		KeepAlive: 30 * time.Second,
	}
	producerDialer = dialer
	topics = topicSet{
		parse: cfg.Topics.Parse,
		chunk: cfg.Topics.Chunk,
		embed: cfg.Topics.Embed,
		index: cfg.Topics.Index,
		dlq:   cfg.Topics.DLQ,
	}
	writers = make(map[string]*kafka.Writer, 5)
	for _, t := range []string{topics.parse, topics.chunk, topics.embed, topics.index, topics.dlq} {
		if _, ok := writers[t]; ok {
			continue
		}
		writers[t] = &kafka.Writer{
			Addr:         kafka.TCP(brokers...),
			Topic:        t,
			Balancer:     &kafka.LeastBytes{},
			RequiredAcks: kafka.RequireOne,
			MaxAttempts:  maxInt(cfg.MaxRetries, 3),
			BatchTimeout: 150 * time.Millisecond,
			ReadTimeout:  20 * time.Second,
			WriteTimeout: 20 * time.Second,
			Transport: &kafka.Transport{
				Dial: func(ctx context.Context, network, address string) (net.Conn, error) {
					return dialer.DialContext(ctx, network, address)
				},
			},
		}
	}
	log.Infof("Kafka 生产者初始化成功, topics=%v", []string{topics.parse, topics.chunk, topics.embed, topics.index, topics.dlq})
}

// topicByStage handles topic by stage.
func topicByStage(stage tasks.Stage) string {
	switch stage {
	case tasks.StageParse:
		return topics.parse
	case tasks.StageChunk:
		return topics.chunk
	case tasks.StageEmbed:
		return topics.embed
	case tasks.StageIndex:
		return topics.index
	default:
		return topics.dlq
	}
}

// produceToTopic handles produce to topic.
func produceToTopic(ctx context.Context, topic string, task tasks.FileProcessingTask) error {
	taskBytes, err := json.Marshal(task)
	if err != nil {
		return err
	}
	return produceBytesToTopic(ctx, topic, taskBytes)
}

func produceBytesToTopic(ctx context.Context, topic string, taskBytes []byte) error {
	if writers == nil {
		return errors.New("kafka producer not initialized")
	}
	writer, ok := writers[topic]
	if !ok {
		return fmt.Errorf("kafka writer for topic '%s' not found", topic)
	}

	maxAttempts := maxInt(producerCfg.MaxRetries, 3)
	var err error
	var lastErr error
	for attempt := 1; attempt <= maxAttempts; attempt++ {
		writeCtx, cancel := context.WithTimeout(ctx, 20*time.Second)
		err = writer.WriteMessages(writeCtx, kafka.Message{
			Time:  time.Now(),
			Value: taskBytes,
		})
		cancel()
		if err == nil {
			return nil
		}

		lastErr = err
		log.Warnf("Kafka writer fallback probe, topic=%s attempt=%d/%d err=%v", topic, attempt, maxAttempts, err)
		if leaderErr := produceByLeaderDial(ctx, topic, taskBytes); leaderErr == nil {
			log.Infof("Kafka leader dial fallback succeeded, topic=%s attempt=%d/%d", topic, attempt, maxAttempts)
			return nil
		} else {
			lastErr = leaderErr
			log.Warnf("Kafka leader dial fallback failed, topic=%s attempt=%d/%d err=%v", topic, attempt, maxAttempts, leaderErr)
		}
		if attempt == maxAttempts || !isRetriableProduceError(err) {
			break
		}

		backoff := time.Duration(maxInt(producerCfg.BaseBackoffMs, 500)) * time.Millisecond * time.Duration(1<<(attempt-1))
		if backoff > 5*time.Second {
			backoff = 5 * time.Second
		}
		log.Warnf("Kafka produce retry, topic=%s attempt=%d/%d backoff=%s err=%v", topic, attempt, maxAttempts, backoff, err)

		select {
		case <-ctx.Done():
			return ctx.Err()
		case <-time.After(backoff):
		}
	}

	return lastErr
}

// produceByLeaderDial handles produce by leader dial.
func produceByLeaderDial(ctx context.Context, topic string, taskBytes []byte) error {
	if producerDialer == nil {
		return errors.New("kafka producer dialer not initialized")
	}

	brokers := parseKafkaBrokers(producerCfg.Brokers)
	var lastErr error
	for _, broker := range brokers {
		writeCtx, cancel := context.WithTimeout(ctx, 20*time.Second)
		conn, err := producerDialer.DialLeader(writeCtx, "tcp", broker, topic, 0)
		cancel()
		if err != nil {
			lastErr = err
			continue
		}

		_ = conn.SetWriteDeadline(time.Now().Add(20 * time.Second))
		_, err = conn.WriteMessages(kafka.Message{
			Time:  time.Now(),
			Value: taskBytes,
		})
		_ = conn.Close()
		if err == nil {
			return nil
		}
		lastErr = err
	}

	if lastErr == nil {
		lastErr = fmt.Errorf("failed to dial kafka leader for topic=%s", topic)
	}
	return lastErr
}

// ProduceFileTask enqueues the first stage of the pipeline (parse).
func ProduceFileTask(task tasks.FileProcessingTask) error {
	if task.Stage == "" {
		task.Stage = tasks.StageParse
	}
	return produceToTopic(context.Background(), topicByStage(task.Stage), task)
}

// ProduceTask handles produce task.
func ProduceTask(task tasks.FileProcessingTask) error {
	return produceToTopic(context.Background(), topicByStage(task.Stage), task)
}

// ProduceTaskToDLQ handles produce task to dlq.
func ProduceTaskToDLQ(task tasks.FileProcessingTask) error {
	return produceToTopic(context.Background(), topics.dlq, task)
}

// ProduceRawToDLQ durably hands off a malformed Kafka payload without attempting to decode it.
func ProduceRawToDLQ(payload []byte) error {
	return produceBytesToTopic(context.Background(), topics.dlq, payload)
}

func handleMalformedMessage(payload []byte, publish func([]byte) error, commit func() error) error {
	if err := publish(payload); err != nil {
		return err
	}
	return commit()
}

func handleSuccessfulMessage(markSuccess func() error, commit func() error) error {
	if err := markSuccess(); err != nil {
		return err
	}
	return commit()
}

// StartPipelineConsumers starts one consumer for each stage topic.
func StartPipelineConsumers(cfg config.KafkaConfig, processor TaskProcessor, tracker repository.PipelineTaskRepository) {
	cfg = normalizeKafkaConfig(cfg)
	go consumeStage(cfg, tracker, processor, tasks.StageParse, cfg.Topics.Parse, cfg.ConsumerGroupPrefix+"-parse")
	go consumeStage(cfg, tracker, processor, tasks.StageChunk, cfg.Topics.Chunk, cfg.ConsumerGroupPrefix+"-chunk")
	go consumeStage(cfg, tracker, processor, tasks.StageEmbed, cfg.Topics.Embed, cfg.ConsumerGroupPrefix+"-embed")
	go consumeStage(cfg, tracker, processor, tasks.StageIndex, cfg.Topics.Index, cfg.ConsumerGroupPrefix+"-index")
}

// consumeStage handles consume stage.
func consumeStage(cfg config.KafkaConfig, tracker repository.PipelineTaskRepository, processor TaskProcessor, stage tasks.Stage, topic, groupID string) {
	consumeStageContext(context.Background(), cfg, tracker, processor, stage, topic, groupID)
}

func consumeStageContext(ctx context.Context, cfg config.KafkaConfig, tracker repository.PipelineTaskRepository, processor TaskProcessor, stage tasks.Stage, topic, groupID string) {
	brokers := parseKafkaBrokers(cfg.Brokers)
	dialer := &kafka.Dialer{
		Timeout:   10 * time.Second,
		DualStack: true,
		KeepAlive: 30 * time.Second,
	}

	readerConfig := consumerReaderConfig(cfg, topic, groupID)
	readerConfig.Brokers = brokers
	readerConfig.Dialer = dialer
	r := kafka.NewReader(readerConfig)
	defer func() {
		if err := r.Close(); err != nil {
			log.Errorf("关闭 Kafka 消费者失败, stage=%s err=%v", stage, err)
		}
	}()

	log.Infof("Kafka 消费者启动, stage=%s topic=%s group=%s", stage, topic, groupID)
	if err := consumeMessagesSerially(
		ctx,
		func(fetchCtx context.Context) (kafka.Message, error) {
			return r.FetchMessage(fetchCtx)
		},
		func(processCtx context.Context, message kafka.Message) error {
			return processStageMessage(processCtx, cfg, r, tracker, processor, stage, topic, message)
		},
		consumerRetryInterval(cfg),
	); err != nil && ctx.Err() == nil {
		log.Errorf("Kafka 消费者停止, stage=%s err=%v", stage, err)
	}
}

func processStageMessage(ctx context.Context, cfg config.KafkaConfig, r *kafka.Reader, tracker repository.PipelineTaskRepository, processor TaskProcessor, stage tasks.Stage, topic string, m kafka.Message) error {
	var task tasks.FileProcessingTask
	if err := json.Unmarshal(m.Value, &task); err != nil {
		log.Errorf("无法解析 Kafka 消息, stage=%s offset=%d err=%v", stage, m.Offset, err)
		published := false
		return retryUntilDurable(ctx, consumerRetryInterval(cfg), func() error {
			if !published {
				if err := ProduceRawToDLQ(m.Value); err != nil {
					return err
				}
				published = true
			}
			return r.CommitMessages(ctx, m)
		})
	}
	if task.Stage == "" {
		task.Stage = stage
	}

	documentVersion := strings.TrimSpace(task.DocumentVersion)
	if documentVersion == "" {
		documentVersion = "upload:" + task.FileMD5
	}
	windowID := pipelineWindowID(task)
	previous, getErr := tracker.GetOrStart(task.FileMD5, documentVersion, string(task.Stage), windowID)
	if getErr != nil {
		log.Errorf("读取任务状态失败, stage=%s file=%s err=%v", stage, task.FileMD5, getErr)
		var err error
		previous, err = retryGetOrStart(ctx, consumerRetryInterval(cfg), tracker, task.FileMD5, documentVersion, string(task.Stage), windowID)
		if err != nil {
			return err
		}
	}
	if previous.Status == model.PipelineStatusSuccess {
		return retryUntilDurable(ctx, consumerRetryInterval(cfg), func() error { return r.CommitMessages(ctx, m) })
	}

	if _, err := retryMarkProcessing(ctx, consumerRetryInterval(cfg), tracker, task.FileMD5, documentVersion, string(task.Stage), windowID); err != nil {
		log.Errorf("标记任务处理中失败, stage=%s file=%s err=%v", stage, task.FileMD5, err)
		return err
	}

	if err := processor.Process(ctx, task); err != nil {
		var retryCount int
		for {
			var markErr error
			retryCount, markErr = tracker.MarkRetryByKey(task.FileMD5, documentVersion, string(task.Stage), windowID, err.Error())
			if markErr == nil {
				break
			}
			log.Errorf("标记任务重试失败, stage=%s file=%s err=%v", stage, task.FileMD5, markErr)
			if !waitForConsumerRetry(ctx, retryDelay(time.Duration(cfg.BaseBackoffMs)*time.Millisecond, 1)) {
				return ctx.Err()
			}
		}

		backoff := retryDelay(time.Duration(cfg.BaseBackoffMs)*time.Millisecond, retryCount)
		if retryCount <= cfg.MaxRetries && !waitForConsumerRetry(ctx, backoff) {
			return ctx.Err()
		}
		for {
			handoffErr := handoffFailedTask(
				tracker,
				task,
				documentVersion,
				windowID,
				retryCount,
				cfg.MaxRetries,
				err,
				func(retryTask tasks.FileProcessingTask) error { return produceToTopic(ctx, topic, retryTask) },
				func(dlqTask tasks.FileProcessingTask) error { return produceToTopic(ctx, topics.dlq, dlqTask) },
			)
			if handoffErr == nil {
				break
			}
			log.Errorf("任务失败交接未完成, stage=%s file=%s backoff=%s err=%v", stage, task.FileMD5, backoff, handoffErr)
			if !waitForConsumerRetry(ctx, backoff) {
				return ctx.Err()
			}
		}
		if retryCount <= cfg.MaxRetries {
			log.Warnf("任务处理失败, stage=%s file=%s retry=%d/%d backoff=%s err=%v", stage, task.FileMD5, retryCount, cfg.MaxRetries, backoff, err)
		} else {
			log.Errorf("任务进入 DLQ, stage=%s file=%s retry=%d", stage, task.FileMD5, retryCount)
		}
		return retryUntilDurable(ctx, consumerRetryInterval(cfg), func() error { return r.CommitMessages(ctx, m) })
	}

	return retryUntilDurable(ctx, consumerRetryInterval(cfg), func() error {
		return handleSuccessfulMessage(
			func() error {
				return tracker.MarkSuccessByKey(task.FileMD5, documentVersion, string(task.Stage), windowID)
			},
			func() error { return r.CommitMessages(ctx, m) },
		)
	})
}

func consumerRetryInterval(cfg config.KafkaConfig) time.Duration {
	return retryDelay(time.Duration(cfg.BaseBackoffMs)*time.Millisecond, 1)
}

func waitForConsumerRetry(ctx context.Context, delay time.Duration) bool {
	select {
	case <-ctx.Done():
		return false
	case <-time.After(delay):
		return true
	}
}

func retryGetOrStart(ctx context.Context, interval time.Duration, tracker repository.PipelineTaskRepository, fileMD5, documentVersion, stage, windowID string) (*model.PipelineTask, error) {
	var task *model.PipelineTask
	err := retryUntilDurable(ctx, interval, func() error {
		var err error
		task, err = tracker.GetOrStart(fileMD5, documentVersion, stage, windowID)
		return err
	})
	return task, err
}

func retryMarkProcessing(ctx context.Context, interval time.Duration, tracker repository.PipelineTaskRepository, fileMD5, documentVersion, stage, windowID string) (*model.PipelineTask, error) {
	var task *model.PipelineTask
	err := retryUntilDurable(ctx, interval, func() error {
		var err error
		task, err = tracker.MarkProcessingByKey(fileMD5, documentVersion, stage, windowID)
		return err
	})
	return task, err
}

// consumerReaderConfig keeps low-volume tasks flowing instead of waiting for a batch-sized fetch.
func consumerReaderConfig(_ config.KafkaConfig, topic, groupID string) kafka.ReaderConfig {
	return kafka.ReaderConfig{
		Topic:    topic,
		GroupID:  groupID,
		MinBytes: 1,
		MaxBytes: 10e6,
	}
}

func pipelineWindowID(task tasks.FileProcessingTask) string {
	if task.Stage == tasks.StageEmbed && task.TaskChunkID > 0 {
		return fmt.Sprintf("window-%d", task.TaskChunkID)
	}
	if value := strings.TrimSpace(task.WindowID); value != "" {
		return value
	}
	return "root"
}

// parseKafkaBrokers handles parse kafka brokers.
func parseKafkaBrokers(raw string) []string {
	parts := strings.Split(raw, ",")
	brokers := make([]string, 0, len(parts))
	for _, part := range parts {
		part = strings.TrimSpace(part)
		if part != "" {
			brokers = append(brokers, part)
		}
	}
	if len(brokers) == 0 {
		return []string{"127.0.0.1:9092"}
	}
	return brokers
}

// isRetriableProduceError reports whether retriable produce error.
func isRetriableProduceError(err error) bool {
	if err == nil {
		return false
	}
	if errors.Is(err, context.DeadlineExceeded) || errors.Is(err, context.Canceled) {
		return true
	}

	var netErr net.Error
	if errors.As(err, &netErr) {
		return true
	}

	msg := strings.ToLower(err.Error())
	return strings.Contains(msg, "i/o timeout") ||
		strings.Contains(msg, "unknown topic or partition") ||
		strings.Contains(msg, "timeout") ||
		strings.Contains(msg, "leader not available") ||
		strings.Contains(msg, "connection reset") ||
		strings.Contains(msg, "broken pipe") ||
		strings.Contains(msg, "unexpected eof")
}

// maxInt returns the larger of two integers.
func maxInt(a, b int) int {
	if a > b {
		return a
	}
	return b
}
