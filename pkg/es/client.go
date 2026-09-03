// Package es contains Elasticsearch helpers.
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
	"strconv"
	"strings"

	"github.com/huadeng408/RAG-High-Availability/internal/config"
	"github.com/huadeng408/RAG-High-Availability/internal/model"
	"github.com/huadeng408/RAG-High-Availability/pkg/log"

	"github.com/elastic/go-elasticsearch/v8"
	"github.com/elastic/go-elasticsearch/v8/esapi"
)

// EvidenceDocument is the searchable source-level evidence stored alongside
// chunk vectors. Evidence IDs include the immutable document version and are
// used as Elasticsearch document IDs for idempotent replay.
type EvidenceDocument struct {
	EvidenceID      string               `json:"evidence_id"`
	FileMD5         string               `json:"file_md5"`
	DocumentVersion string               `json:"document_version"`
	Modality        string               `json:"modality"`
	PageNumber      int                  `json:"page_number,omitempty"`
	SlideNumber     int                  `json:"slide_number,omitempty"`
	SheetName       string               `json:"sheet_name,omitempty"`
	RowStart        int                  `json:"row_start,omitempty"`
	RowEnd          int                  `json:"row_end,omitempty"`
	HeadingPath     []string             `json:"heading_path,omitempty"`
	TextContent     string               `json:"text_content"`
	SourceAsset     string               `json:"source_asset"`
	BBox            *model.BoundingBox   `json:"bbox,omitempty"`
	Image           *model.ImageMetadata `json:"image,omitempty"`
	OwnerID         string               `json:"owner_id"`
	OrgTag          string               `json:"org_tag"`
	IsPublic        bool                 `json:"is_public"`
}

// BuildEvidenceDocuments maps persisted source evidence to its searchable form.
func BuildEvidenceDocuments(fileMD5 string, ownerID uint, orgTag string, isPublic bool, evidence []model.EvidenceUnit) []EvidenceDocument {
	docs := make([]EvidenceDocument, 0, len(evidence))
	for _, unit := range evidence {
		if strings.TrimSpace(unit.ID) == "" || strings.TrimSpace(unit.DocumentVersion) == "" {
			continue
		}
		docs = append(docs, EvidenceDocument{
			EvidenceID: unit.ID, FileMD5: fileMD5, DocumentVersion: unit.DocumentVersion,
			Modality: unit.Modality, PageNumber: unit.Page, SlideNumber: unit.Slide,
			SheetName: unit.Sheet, RowStart: unit.RowStart, RowEnd: unit.RowEnd, HeadingPath: unit.HeadingPath,
			TextContent: unit.Text, SourceAsset: unit.AssetPath, BBox: unit.BBox, Image: unit.Image,
			OwnerID: strconv.FormatUint(uint64(ownerID), 10), OrgTag: orgTag, IsPublic: isPublic,
		})
	}
	return docs
}

// BulkIndexEvidenceDocuments writes source evidence with stable IDs so retries
// and replay overwrite the same records instead of creating duplicates.
func BulkIndexEvidenceDocuments(ctx context.Context, indexName string, docs []EvidenceDocument) error {
	if len(docs) == 0 {
		return nil
	}
	var buf bytes.Buffer
	for _, doc := range docs {
		meta, err := json.Marshal(map[string]any{"index": map[string]string{"_index": indexName, "_id": doc.EvidenceID}})
		if err != nil {
			return err
		}
		body, err := json.Marshal(doc)
		if err != nil {
			return err
		}
		buf.Write(meta)
		buf.WriteByte('\n')
		buf.Write(body)
		buf.WriteByte('\n')
	}
	res, err := (esapi.BulkRequest{Index: indexName, Body: &buf, Refresh: "true"}).Do(ctx, ESClient)
	if err != nil {
		return err
	}
	defer res.Body.Close()
	if res.IsError() {
		body, _ := io.ReadAll(res.Body)
		return fmt.Errorf("bulk evidence index failed: status=%s body=%s", res.Status(), strings.TrimSpace(string(body)))
	}
	var response struct {
		Errors bool `json:"errors"`
	}
	if err := json.NewDecoder(res.Body).Decode(&response); err != nil {
		return err
	}
	if response.Errors {
		return errors.New("bulk evidence index completed with partial failures")
	}
	return nil
}

// ESClient stores the shared Elasticsearch client instance.
var ESClient *elasticsearch.Client

// InitES initializes the shared Elasticsearch client and ensures the main document index exists.
func InitES(esCfg config.ElasticsearchConfig, vectorDims int) error {
	if vectorDims <= 0 {
		vectorDims = 2048
	}

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
	if strings.TrimSpace(esCfg.IndexName) == "" || strings.TrimSpace(esCfg.IndexName) == KnowledgeReadAlias {
		return EnsureRHAIndices(context.Background(), vectorDims, esCfg.IndexGeneration)
	}
	return createIndexIfNotExists(esCfg.IndexName, vectorDims)
}

// EnsureMemoryIndex creates the long-term memory index when memory support is enabled.
func EnsureMemoryIndex(indexName string, vectorDims int) error {
	if strings.TrimSpace(indexName) == "" {
		return nil
	}
	if vectorDims <= 0 {
		vectorDims = 2048
	}

	res, err := ESClient.Indices.Exists([]string{indexName})
	if err != nil {
		log.Errorf("failed to check memory index existence: %v", err)
		return err
	}
	defer res.Body.Close()

	if !res.IsError() && res.StatusCode == http.StatusOK {
		log.Infof("memory index '%s' already exists", indexName)
		return nil
	}
	if res.StatusCode != http.StatusNotFound {
		return fmt.Errorf("unexpected status when checking memory index existence: %d", res.StatusCode)
	}

	mapping := fmt.Sprintf(`{
		"mappings": {
			"properties": {
				"memory_id": { "type": "keyword" },
				"user_id": { "type": "long" },
				"conversation_id": { "type": "keyword" },
				"memory_type": { "type": "keyword" },
				"text_content": {
					"type": "text",
					"analyzer": "standard",
					"search_analyzer": "standard"
				},
				"vector": {
					"type": "dense_vector",
					"dims": %d,
					"index": true,
					"similarity": "cosine"
				},
				"importance": { "type": "float" },
				"created_at": { "type": "date" }
			}
		}
	}`, vectorDims)

	res, err = ESClient.Indices.Create(indexName, ESClient.Indices.Create.WithBody(strings.NewReader(mapping)))
	if err != nil {
		return err
	}
	defer res.Body.Close()
	if res.IsError() {
		bodyBytes, _ := io.ReadAll(res.Body)
		return fmt.Errorf("failed to create memory index: status=%s body=%s", res.Status(), strings.TrimSpace(string(bodyBytes)))
	}
	return nil
}

// createIndexIfNotExists creates the main document index if it does not already exist.
func createIndexIfNotExists(indexName string, vectorDims int) error {
	res, err := ESClient.Indices.Exists([]string{indexName})
	if err != nil {
		log.Errorf("failed to check index existence: %v", err)
		return err
	}
	defer res.Body.Close()

	if !res.IsError() && res.StatusCode == http.StatusOK {
		log.Infof("index '%s' already exists", indexName)
		dims, dimErr := GetIndexVectorDims(context.Background(), indexName, "vector")
		if dimErr == nil && dims > 0 && dims != vectorDims {
			log.Warnf("index '%s' vector dims=%d but embedding dims=%d; vector search may degrade to keyword-only fallback", indexName, dims, vectorDims)
		}
		return nil
	}
	if res.StatusCode != http.StatusNotFound {
		return fmt.Errorf("unexpected status when checking index existence: %d", res.StatusCode)
	}

	mapping := fmt.Sprintf(`{
		"mappings": {
			"properties": {
				"vector_id": { "type": "keyword" },
				"file_md5": { "type": "keyword" },
				"document_version": { "type": "keyword" },
				"chunk_id": { "type": "integer" },
				"text_content": {
					"type": "text",
					"analyzer": "standard",
					"search_analyzer": "standard"
				},
				"vector": {
					"type": "dense_vector",
					"dims": %d,
					"index": true,
					"similarity": "cosine"
				},
				"model_version": { "type": "keyword" },
				"user_id": { "type": "long" },
				"org_tag": { "type": "keyword" },
				"is_public": { "type": "boolean" },
				"modality": { "type": "keyword" },
				"page": { "type": "integer" },
				"slide": { "type": "integer" },
				"sheet": { "type": "keyword" },
				"evidence_ids": { "type": "keyword" },
				"bbox": { "type": "object" },
				"image": {
					"properties": {
						"assetSha256": { "type": "keyword" },
						"mimeType": { "type": "keyword" },
						"width": { "type": "integer" },
						"height": { "type": "integer" },
						"orientationNormalized": { "type": "boolean" },
						"ocrConfidence": { "type": "float" },
						"visionModel": { "type": "keyword" }
					}
				}
			}
		}
	}`, vectorDims)

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

// GetIndexVectorDims reads the configured dense-vector dimension from an index mapping.
func GetIndexVectorDims(ctx context.Context, indexName, fieldName string) (int, error) {
	res, err := ESClient.Indices.GetMapping(ESClient.Indices.GetMapping.WithContext(ctx), ESClient.Indices.GetMapping.WithIndex(indexName))
	if err != nil {
		return 0, err
	}
	defer res.Body.Close()
	if res.IsError() {
		body, _ := io.ReadAll(res.Body)
		return 0, fmt.Errorf("get mapping failed: status=%s body=%s", res.Status(), strings.TrimSpace(string(body)))
	}

	var mapping map[string]struct {
		Mappings struct {
			Properties map[string]struct {
				Dims any `json:"dims"`
			} `json:"properties"`
		} `json:"mappings"`
	}
	if err := json.NewDecoder(res.Body).Decode(&mapping); err != nil {
		return 0, err
	}
	idx, ok := mapping[indexName]
	if !ok {
		for _, v := range mapping {
			idx = v
			break
		}
	}
	field, ok := idx.Mappings.Properties[fieldName]
	if !ok {
		return 0, nil
	}
	switch d := field.Dims.(type) {
	case float64:
		return int(d), nil
	case int:
		return d, nil
	case json.Number:
		v, _ := d.Int64()
		return int(v), nil
	case string:
		v, convErr := strconv.Atoi(d)
		if convErr != nil {
			return 0, convErr
		}
		return v, nil
	default:
		return 0, nil
	}
}

// IndexDocument writes one knowledge document into Elasticsearch.
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

// IndexMemoryDocument writes one memory document into Elasticsearch.
func IndexMemoryDocument(ctx context.Context, indexName string, doc model.MemoryEsDocument) error {
	docBytes, err := json.Marshal(doc)
	if err != nil {
		return err
	}

	req := esapi.IndexRequest{
		Index:      indexName,
		DocumentID: doc.MemoryID,
		Body:       bytes.NewReader(docBytes),
		Refresh:    "true",
	}
	res, err := req.Do(ctx, ESClient)
	if err != nil {
		return err
	}
	defer res.Body.Close()
	if res.IsError() {
		bodyBytes, _ := io.ReadAll(res.Body)
		return fmt.Errorf("failed to index memory document: status=%s body=%s", res.Status(), strings.TrimSpace(string(bodyBytes)))
	}
	return nil
}

// BulkIndexDocuments writes multiple knowledge documents with the Elasticsearch bulk API.
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

// DeleteDocumentsByFileMD5 removes all indexed chunks for one uploaded file.
func DeleteDocumentsByFileMD5(ctx context.Context, indexName, fileMD5 string) error {
	if strings.TrimSpace(indexName) == "" {
		return errors.New("elasticsearch index name is empty")
	}
	if strings.TrimSpace(fileMD5) == "" {
		return errors.New("file md5 is empty")
	}

	body := map[string]any{
		"query": map[string]any{
			"term": map[string]any{
				"file_md5": fileMD5,
			},
		},
	}
	bodyBytes, err := json.Marshal(body)
	if err != nil {
		return err
	}

	refresh := true
	allowNoIndices := true
	ignoreUnavailable := true
	req := esapi.DeleteByQueryRequest{
		Index:             []string{indexName},
		Body:              bytes.NewReader(bodyBytes),
		Conflicts:         "proceed",
		Refresh:           &refresh,
		AllowNoIndices:    &allowNoIndices,
		IgnoreUnavailable: &ignoreUnavailable,
	}
	res, err := req.Do(ctx, ESClient)
	if err != nil {
		return err
	}
	defer res.Body.Close()
	if res.IsError() {
		bodyBytes, _ := io.ReadAll(res.Body)
		return fmt.Errorf("delete documents by file_md5 failed: status=%s body=%s", res.Status(), strings.TrimSpace(string(bodyBytes)))
	}
	return nil
}
