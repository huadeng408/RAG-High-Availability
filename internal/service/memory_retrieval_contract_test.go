package service

import (
	"encoding/json"
	"reflect"
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

func TestDedupeContextSnippetsPreservesRRFInputRanking(t *testing.T) {
	ids := []string{
		"knowledge-01", "memory-01", "knowledge-02", "knowledge-03",
		"knowledge-04", "knowledge-05", "knowledge-06", "knowledge-07",
		"knowledge-08", "knowledge-09", "knowledge-10", "knowledge-11",
		"knowledge-12", "knowledge-13", "knowledge-14", "knowledge-15",
	}
	items := make([]ContextSnippet, 0, len(ids)+1)
	for _, id := range ids {
		items = append(items, ContextSnippet{ID: id, SourceType: "context", Text: id, Score: 0.5})
	}
	items = append(items, ContextSnippet{ID: "memory-01", SourceType: "context", Text: "updated memory", Score: 0.9})

	got := dedupeContextSnippets(items)
	gotIDs := make([]string, 0, len(got))
	for _, item := range got {
		gotIDs = append(gotIDs, item.ID)
	}
	if !reflect.DeepEqual(gotIDs, ids) {
		t.Fatalf("dedupe changed ranked order: got %v, want %v", gotIDs, ids)
	}
	if got[1].Text != "updated memory" || got[1].Score != 0.9 {
		t.Fatalf("higher-scored duplicate did not replace in place: %#v", got[1])
	}
}
