// Package embedding provides a client for interacting with embedding models.
package embedding

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"pai-smart-go/internal/config"
	"pai-smart-go/pkg/log"
	"strings"
)

// Client defines the interface for an embedding client.
type Client interface {
	CreateEmbedding(ctx context.Context, text string) ([]float32, error)
	CreateEmbeddings(ctx context.Context, texts []string) ([][]float32, error)
}

type openAICompatibleClient struct {
	cfg    config.EmbeddingConfig
	client *http.Client
}

// NewClient creates a new embedding client based on the provider in the config.
func NewClient(cfg config.EmbeddingConfig) Client {
	return &openAICompatibleClient{
		cfg:    cfg,
		client: &http.Client{},
	}
}

type embeddingRequest struct {
	Model      string   `json:"model"`
	Input      []string `json:"input"`
	Dimensions int      `json:"dimensions,omitempty"`
}

type embeddingResponse struct {
	Data []struct {
		Embedding []float32 `json:"embedding"`
	} `json:"data"`
}

// CreateEmbedding calls the OpenAI-compatible API to get the vector for a given text.
func (c *openAICompatibleClient) CreateEmbedding(ctx context.Context, text string) ([]float32, error) {
	vectors, err := c.CreateEmbeddings(ctx, []string{text})
	if err != nil {
		return nil, err
	}
	if len(vectors) == 0 {
		return nil, fmt.Errorf("received empty embedding from api")
	}
	return vectors[0], nil
}

func (c *openAICompatibleClient) CreateEmbeddings(ctx context.Context, texts []string) ([][]float32, error) {
	if len(texts) == 0 {
		return [][]float32{}, nil
	}
	log.Infof("[EmbeddingClient] 开始调用 Embedding API, model: %s, batch_size: %d", c.cfg.Model, len(texts))
	reqBody := embeddingRequest{
		Model: c.cfg.Model, // Use model from config
		Input: texts,
	}
	if shouldSendDimensions(c.cfg) {
		reqBody.Dimensions = c.cfg.Dimensions
	}

	reqBytes, err := json.Marshal(reqBody)
	if err != nil {
		return nil, fmt.Errorf("failed to marshal embedding request: %w", err)
	}

	req, err := http.NewRequestWithContext(ctx, "POST", c.cfg.BaseURL+"/embeddings", bytes.NewReader(reqBytes))
	if err != nil {
		return nil, fmt.Errorf("failed to create embedding request: %w", err)
	}

	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("Authorization", "Bearer "+c.cfg.APIKey)

	resp, err := c.client.Do(req)
	if err != nil {
		log.Errorf("[EmbeddingClient] 调用 Embedding API 失败, error: %v", err)
		return nil, fmt.Errorf("failed to call embedding api: %w", err)
	}
	defer resp.Body.Close()

	if resp.StatusCode != http.StatusOK {
		body, _ := io.ReadAll(resp.Body)
		bodyText := strings.TrimSpace(string(body))
		log.Errorf("[EmbeddingClient] Embedding API 返回非 200 状态码: %s, body: %s", resp.Status, bodyText)
		if bodyText == "" {
			return nil, fmt.Errorf("embedding api returned non-200 status: %s", resp.Status)
		}
		return nil, fmt.Errorf("embedding api returned non-200 status: %s, body: %s", resp.Status, bodyText)
	}

	var embeddingResp embeddingResponse
	if err := json.NewDecoder(resp.Body).Decode(&embeddingResp); err != nil {
		log.Errorf("[EmbeddingClient] 解析 Embedding API 响应失败, error: %v", err)
		return nil, fmt.Errorf("failed to decode embedding response: %w", err)
	}

	if len(embeddingResp.Data) == 0 || len(embeddingResp.Data[0].Embedding) == 0 {
		log.Warnf("[EmbeddingClient] Embedding API 返回了空的向量数据")
		return nil, fmt.Errorf("received empty embedding from api")
	}

	vectors := make([][]float32, 0, len(embeddingResp.Data))
	for _, item := range embeddingResp.Data {
		if len(item.Embedding) == 0 {
			return nil, fmt.Errorf("received empty embedding item from api")
		}
		vectors = append(vectors, item.Embedding)
	}
	log.Infof("[EmbeddingClient] 成功从 Embedding API 获取向量, count: %d, dim: %d", len(vectors), len(vectors[0]))
	return vectors, nil
}

func shouldSendDimensions(cfg config.EmbeddingConfig) bool {
	if cfg.Dimensions <= 0 {
		return false
	}

	baseURL := strings.ToLower(cfg.BaseURL)
	model := strings.ToLower(cfg.Model)

	if strings.Contains(baseURL, "dashscope.aliyuncs.com") || strings.Contains(model, "text-embedding-v4") {
		return false
	}

	return true
}
