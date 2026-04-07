package agent

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"strings"
	"time"

	"pai-smart-go/internal/config"
)

type Message struct {
	Role    string `json:"role"`
	Content string `json:"content"`
}

type CompletionParams struct {
	Temperature *float64 `json:"temperature,omitempty"`
	MaxTokens   *int     `json:"max_tokens,omitempty"`
}

type Client interface {
	Enabled() bool
	Complete(ctx context.Context, messages []Message, params *CompletionParams) (string, error)
}

type noopClient struct{}

func (noopClient) Enabled() bool {
	return false
}

func (noopClient) Complete(ctx context.Context, messages []Message, params *CompletionParams) (string, error) {
	return "", fmt.Errorf("agent client is disabled")
}

type openAIClient struct {
	cfg    config.AgentConfig
	client *http.Client
}

type nativeOllamaClient struct {
	cfg    config.AgentConfig
	client *http.Client
}

type completionRequest struct {
	Model       string    `json:"model"`
	Messages    []Message `json:"messages"`
	Temperature *float64  `json:"temperature,omitempty"`
	MaxTokens   *int      `json:"max_tokens,omitempty"`
	Stream      bool      `json:"stream"`
}

type completionResponse struct {
	Choices []struct {
		Message struct {
			Content string `json:"content"`
		} `json:"message"`
	} `json:"choices"`
}

type nativeChatRequest struct {
	Model    string         `json:"model"`
	Messages []Message      `json:"messages"`
	Stream   bool           `json:"stream"`
	Format   string         `json:"format,omitempty"`
	Options  map[string]any `json:"options,omitempty"`
}

type nativeChatResponse struct {
	Message struct {
		Content string `json:"content"`
	} `json:"message"`
	Response string `json:"response"`
	Thinking string `json:"thinking"`
}

func NewClient(cfg config.AgentConfig) Client {
	if !cfg.Enabled {
		return noopClient{}
	}
	baseURL := strings.TrimSpace(cfg.BaseURL)
	if baseURL == "" || strings.TrimSpace(cfg.Model) == "" {
		return noopClient{}
	}

	timeout := time.Duration(cfg.TimeoutMs) * time.Millisecond
	if timeout <= 0 {
		timeout = 1500 * time.Millisecond
	}

	if cfg.NativeOllama || strings.Contains(baseURL, ":11434") {
		return &nativeOllamaClient{
			cfg: cfg,
			client: &http.Client{
				Timeout: timeout,
			},
		}
	}

	return &openAIClient{
		cfg: cfg,
		client: &http.Client{
			Timeout: timeout,
		},
	}
}

func (c *openAIClient) Enabled() bool {
	return true
}

func (c *nativeOllamaClient) Enabled() bool {
	return true
}

func (c *openAIClient) Complete(ctx context.Context, messages []Message, params *CompletionParams) (string, error) {
	reqBody := completionRequest{
		Model:    c.cfg.Model,
		Messages: messages,
		Stream:   false,
	}
	if params != nil {
		reqBody.Temperature = params.Temperature
		reqBody.MaxTokens = params.MaxTokens
	}

	bodyBytes, err := json.Marshal(reqBody)
	if err != nil {
		return "", fmt.Errorf("marshal agent request failed: %w", err)
	}

	baseURL := strings.TrimRight(strings.TrimSpace(c.cfg.BaseURL), "/")
	req, err := http.NewRequestWithContext(ctx, http.MethodPost, baseURL+"/chat/completions", bytes.NewReader(bodyBytes))
	if err != nil {
		return "", fmt.Errorf("create agent request failed: %w", err)
	}
	req.Header.Set("Content-Type", "application/json")
	if token := strings.TrimSpace(c.cfg.APIKey); token != "" {
		req.Header.Set("Authorization", "Bearer "+token)
	}

	resp, err := c.client.Do(req)
	if err != nil {
		return "", fmt.Errorf("call agent api failed: %w", err)
	}
	defer resp.Body.Close()

	if resp.StatusCode != http.StatusOK {
		raw, _ := io.ReadAll(resp.Body)
		return "", fmt.Errorf("agent api returned status=%s body=%s", resp.Status, strings.TrimSpace(string(raw)))
	}

	var parsed completionResponse
	if err := json.NewDecoder(resp.Body).Decode(&parsed); err != nil {
		return "", fmt.Errorf("decode agent response failed: %w", err)
	}
	if len(parsed.Choices) == 0 {
		return "", fmt.Errorf("agent response choices are empty")
	}

	content := strings.TrimSpace(parsed.Choices[0].Message.Content)
	if content == "" {
		return "", fmt.Errorf("agent response content is empty")
	}
	return content, nil
}

func (c *nativeOllamaClient) Complete(ctx context.Context, messages []Message, params *CompletionParams) (string, error) {
	options := map[string]any{}
	if c.cfg.ContextWindow > 0 {
		options["num_ctx"] = c.cfg.ContextWindow
	}
	if params != nil && params.Temperature != nil {
		options["temperature"] = *params.Temperature
	}
	if params != nil && params.MaxTokens != nil {
		options["num_predict"] = *params.MaxTokens
	}

	reqBody := nativeChatRequest{
		Model:    c.cfg.Model,
		Messages: messages,
		Stream:   false,
		Format:   "json",
	}
	if len(options) > 0 {
		reqBody.Options = options
	}

	bodyBytes, err := json.Marshal(reqBody)
	if err != nil {
		return "", fmt.Errorf("marshal native ollama request failed: %w", err)
	}

	baseURL := strings.TrimRight(strings.TrimSpace(c.cfg.BaseURL), "/")
	req, err := http.NewRequestWithContext(ctx, http.MethodPost, baseURL+"/api/chat", bytes.NewReader(bodyBytes))
	if err != nil {
		return "", fmt.Errorf("create native ollama request failed: %w", err)
	}
	req.Header.Set("Content-Type", "application/json")

	resp, err := c.client.Do(req)
	if err != nil {
		return "", fmt.Errorf("call native ollama api failed: %w", err)
	}
	defer resp.Body.Close()

	if resp.StatusCode != http.StatusOK {
		raw, _ := io.ReadAll(resp.Body)
		return "", fmt.Errorf("native ollama api returned status=%s body=%s", resp.Status, strings.TrimSpace(string(raw)))
	}

	var parsed nativeChatResponse
	if err := json.NewDecoder(resp.Body).Decode(&parsed); err != nil {
		return "", fmt.Errorf("decode native ollama response failed: %w", err)
	}

	content := strings.TrimSpace(parsed.Message.Content)
	if content == "" {
		content = strings.TrimSpace(parsed.Response)
	}
	if content == "" {
		content = strings.TrimSpace(parsed.Thinking)
	}
	return content, nil
}
