package service

import (
	"encoding/json"
	"testing"

	"github.com/huadeng408/RAG-High-Availability/internal/model"
)

func TestExplicitLongTermRecallWorksWithoutShortTermHistory(t *testing.T) {
	plan := model.AgentPlan{RetrievalMode: model.RetrievalModeHybrid}
	if !shouldRetrieveLongTermMemory("What durable project marker did I ask you to remember?", plan, nil) {
		t.Fatal("explicit recall must search durable memory even when short-term history is empty")
	}
}

func TestMemorySnippetSerializesEmptyCitationsAsArray(t *testing.T) {
	items := convertContextSnippets([]ContextSnippet{{ID: "memory-1", SourceType: "memory", Text: "marker"}})
	payload, err := json.Marshal(items)
	if err != nil {
		t.Fatal(err)
	}
	if string(payload) != `[{"id":"memory-1","sourceType":"memory","label":"","text":"marker","score":0,"timestamp":"0001-01-01T00:00:00Z","citations":[]}]` {
		t.Fatalf("memory snippet contract contains nullable citations: %s", payload)
	}
}
