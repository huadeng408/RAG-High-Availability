package es

import (
	"bufio"
	"bytes"
	"context"
	"crypto/tls"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"strings"

	"pai-smart-go/internal/config"
	"pai-smart-go/internal/model"
	"pai-smart-go/pkg/log"

	"github.com/elastic/go-elasticsearch/v8"
	"github.com/elastic/go-elasticsearch/v8/esapi"
)

var ESClient *elasticsearch.Client

func InitES(esCfg config.ElasticsearchConfig) error {
	cfg := elasticsearch.Config{
		Addresses: []string{esCfg.Addresses},
		Username:  esCfg.Username,
		Password:  esCfg.Password,
		Transport: &http.Transport{TLSClientConfig: &tls.Config{InsecureSkipVerify: true}},
	}
	client, err := elasticsearch.NewClient(cfg)
	if err != nil {
		return err
	}
	ESClient = client
	return createIndexIfNotExists(esCfg.IndexName)
}

func createIndexIfNotExists(indexName string) error {
	res, err := ESClient.Indices.Exists([]string{indexName})
	if err != nil {
		log.Errorf("failed to check index existence: %v", err)
		return err
	}
	defer res.Body.Close()

	if !res.IsError() && res.StatusCode == http.StatusOK {
		log.Infof("index '%s' already exists", indexName)
		return nil
	}
	if res.StatusCode != http.StatusNotFound {
		return fmt.Errorf("unexpected status when checking index existence: %d", res.StatusCode)
	}

	mapping := `{
		"mappings": {
			"properties": {
				"vector_id": { "type": "keyword" },
				"file_md5": { "type": "keyword" },
				"chunk_id": { "type": "integer" },
				"text_content": {
					"type": "text",
					"analyzer": "ik_max_word",
					"search_analyzer": "ik_smart"
				},
				"vector": {
					"type": "dense_vector",
					"dims": 2048,
					"index": true,
					"similarity": "cosine"
				},
				"model_version": { "type": "keyword" },
				"user_id": { "type": "long" },
				"org_tag": { "type": "keyword" },
				"is_public": { "type": "boolean" }
			}
		}
	}`

	res, err = ESClient.Indices.Create(indexName, ESClient.Indices.Create.WithBody(strings.NewReader(mapping)))
	if err != nil {
		return err
	}
	defer res.Body.Close()
	if res.IsError() {
		return errors.New("failed to create index")
	}
	return nil
}

func IndexDocument(ctx context.Context, indexName string, doc model.EsDocument) error {
	docBytes, err := json.Marshal(doc)
	if err != nil {
		return err
	}

	req := esapi.IndexRequest{
		Index:      indexName,
		DocumentID: doc.VectorID,
		Body:       bytes.NewReader(docBytes),
		Refresh:    "true",
	}
	res, err := req.Do(ctx, ESClient)
	if err != nil {
		return err
	}
	defer res.Body.Close()
	if res.IsError() {
		return errors.New("failed to index document")
	}
	return nil
}

func BulkIndexDocuments(ctx context.Context, indexName string, docs []model.EsDocument) error {
	if len(docs) == 0 {
		return nil
	}

	var buf bytes.Buffer
	w := bufio.NewWriter(&buf)
	for _, doc := range docs {
		metaLine := fmt.Sprintf("{\"index\":{\"_index\":\"%s\",\"_id\":\"%s\"}}\n", indexName, doc.VectorID)
		if _, err := w.WriteString(metaLine); err != nil {
			return err
		}
		bodyBytes, err := json.Marshal(doc)
		if err != nil {
			return err
		}
		if _, err := w.Write(bodyBytes); err != nil {
			return err
		}
		if _, err := w.WriteString("\n"); err != nil {
			return err
		}
	}
	if err := w.Flush(); err != nil {
		return err
	}

	req := esapi.BulkRequest{Index: indexName, Body: &buf, Refresh: "true"}
	res, err := req.Do(ctx, ESClient)
	if err != nil {
		return err
	}
	defer res.Body.Close()

	if res.IsError() {
		bodyBytes, _ := io.ReadAll(res.Body)
		log.Errorf("bulk index failed: status=%s body=%s", res.Status(), string(bodyBytes))
		return errors.New("failed to bulk index documents")
	}

	var bulkResp struct {
		Errors bool `json:"errors"`
	}
	if err := json.NewDecoder(res.Body).Decode(&bulkResp); err != nil {
		return err
	}
	if bulkResp.Errors {
		return errors.New("bulk index completed with partial failures")
	}
	return nil
}
