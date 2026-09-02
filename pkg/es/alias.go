package es

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"strings"
)

const (
	KnowledgePhysicalIndex = "rha-knowledge-v1"
	EvidencePhysicalIndex  = "rha-evidence-v1"
	KnowledgeReadAlias     = "rha-knowledge-active"
	EvidenceReadAlias      = "rha-evidence-active"
)

// EnsureRHAIndices creates the versioned knowledge/evidence indices and points
// the read aliases at the known-good physical generation.
func EnsureRHAIndices(ctx context.Context, vectorDims int) error {
	if ESClient == nil {
		return fmt.Errorf("elasticsearch client is not initialized")
	}
	if err := createIndexIfNotExists(KnowledgePhysicalIndex, vectorDims); err != nil {
		return err
	}
	if err := createEvidenceIndexIfNotExists(EvidencePhysicalIndex); err != nil {
		return err
	}
	if err := SwitchAlias(ctx, KnowledgeReadAlias, KnowledgePhysicalIndex); err != nil {
		return err
	}
	return SwitchAlias(ctx, EvidenceReadAlias, EvidencePhysicalIndex)
}

func createEvidenceIndexIfNotExists(indexName string) error {
	res, err := ESClient.Indices.Exists([]string{indexName})
	if err != nil {
		return err
	}
	defer res.Body.Close()
	if !res.IsError() && res.StatusCode == 200 {
		return nil
	}
	if res.StatusCode != 404 {
		return fmt.Errorf("unexpected status when checking evidence index existence: %d", res.StatusCode)
	}
	mapping := `{"mappings":{"properties":{"evidence_id":{"type":"keyword"},"document_version":{"type":"keyword"},"modality":{"type":"keyword"},"page_number":{"type":"integer"},"slide_number":{"type":"integer"},"sheet_name":{"type":"keyword"},"text_content":{"type":"text"},"bbox":{"type":"object"},"image":{"type":"object"},"owner_id":{"type":"keyword"},"org_tag":{"type":"keyword"},"is_public":{"type":"boolean"}}}}`
	res, err = ESClient.Indices.Create(indexName, ESClient.Indices.Create.WithBody(strings.NewReader(mapping)))
	if err != nil {
		return err
	}
	defer res.Body.Close()
	if res.IsError() {
		body, _ := io.ReadAll(res.Body)
		return fmt.Errorf("create evidence index failed: status=%s body=%s", res.Status(), strings.TrimSpace(string(body)))
	}
	return nil
}

func aliasSwitchBody(alias, nextIndex string) ([]byte, error) {
	if strings.TrimSpace(alias) == "" || strings.TrimSpace(nextIndex) == "" {
		return nil, fmt.Errorf("alias and next index are required")
	}
	return json.Marshal(map[string]any{"actions": []any{
		map[string]any{"remove": map[string]any{"index": "*", "alias": alias, "must_exist": false}},
		map[string]any{"add": map[string]any{"index": nextIndex, "alias": alias}},
	}})
}

// SwitchAlias atomically points a read alias at a verified physical index.
func SwitchAlias(ctx context.Context, alias, nextIndex string) error {
	if ESClient == nil {
		return fmt.Errorf("elasticsearch client is not initialized")
	}
	body, err := aliasSwitchBody(alias, nextIndex)
	if err != nil {
		return err
	}
	res, err := ESClient.Indices.UpdateAliases(bytes.NewReader(body), ESClient.Indices.UpdateAliases.WithContext(ctx))
	if err != nil {
		return err
	}
	defer res.Body.Close()
	if res.IsError() {
		response, _ := io.ReadAll(res.Body)
		return fmt.Errorf("switch alias failed: status=%s body=%s", res.Status(), strings.TrimSpace(string(response)))
	}
	return nil
}
