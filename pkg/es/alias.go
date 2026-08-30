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
