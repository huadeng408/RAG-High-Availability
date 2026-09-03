package service

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"reflect"
	"testing"

	"github.com/elastic/go-elasticsearch/v8"
	"github.com/huadeng408/RAG-High-Availability/internal/config"
	"github.com/huadeng408/RAG-High-Availability/internal/model"
	"github.com/huadeng408/RAG-High-Availability/internal/repository"
	"github.com/huadeng408/RAG-High-Availability/pkg/log"
)

type failedEmbeddingClient struct{}

func (failedEmbeddingClient) CreateEmbedding(context.Context, string) ([]float32, error) {
	return nil, context.DeadlineExceeded
}

func (failedEmbeddingClient) CreateEmbeddings(context.Context, []string) ([][]float32, error) {
	return nil, context.DeadlineExceeded
}

type reliabilityUserService struct {
	UserService
	tags []string
}

func (s reliabilityUserService) GetUserEffectiveOrgTags(*model.User) ([]string, error) {
	return s.tags, nil
}

type reliabilityUploadRepository struct {
	repository.UploadRepository
}

func (reliabilityUploadRepository) FindBatchByMD5s([]string) ([]*model.FileUpload, error) {
	return []*model.FileUpload{{FileMD5: "permitted-file", FileName: "permitted.txt"}}, nil
}

func TestHybridSearchKeepsLexicalHitWhenEmbeddingTimesOut(t *testing.T) {
	log.Init("error", "json", "")
	var searchBody map[string]any
	server := httptest.NewServer(http.HandlerFunc(func(writer http.ResponseWriter, request *http.Request) {
		defer request.Body.Close()
		writer.Header().Set("X-Elastic-Product", "Elasticsearch")
		if err := json.NewDecoder(request.Body).Decode(&searchBody); err != nil {
			t.Errorf("decode search body: %v", err)
			return
		}
		_ = json.NewEncoder(writer).Encode(map[string]any{
			"hits": map[string]any{"hits": []any{map[string]any{
				"_id": "lexical-hit", "_score": 1.0,
				"_source": map[string]any{
					"file_md5": "permitted-file", "chunk_id": 1, "text_content": "lexical fallback",
					"user_id": 7, "org_tag": "finance", "is_public": false,
				},
			}}},
		})
	}))
	defer server.Close()

	client, err := elasticsearch.NewClient(elasticsearch.Config{Addresses: []string{server.URL}})
	if err != nil {
		t.Fatal(err)
	}
	search := NewSearchService(
		failedEmbeddingClient{}, nil, client,
		reliabilityUserService{tags: []string{"finance"}}, reliabilityUploadRepository{},
		"rha-knowledge-active", config.RetrievalConfig{BM25TopN: 5, VectorTopN: 5, RRFK: 60, FinalTopK: 5},
	)

	results, err := search.HybridSearch(context.Background(), "retention", 5, &model.User{ID: 7, Username: "owner"})
	if err != nil {
		t.Fatal(err)
	}
	if len(results) != 1 || results[0].FileMD5 != "permitted-file" || results[0].TextContent != "lexical fallback" {
		t.Fatalf("lexical fallback lost after embedding timeout: %#v", results)
	}
	query, _ := searchBody["query"].(map[string]any)
	boolQuery, _ := query["bool"].(map[string]any)
	filters, _ := boolQuery["filter"].([]any)
	permissionJSON, _ := json.Marshal(filters)
	if string(permissionJSON) != `[{"bool":{"minimum_should_match":1,"should":[{"term":{"user_id":7}},{"term":{"is_public":true}},{"terms":{"org_tag":["finance"]}}]}}]` {
		t.Fatalf("lexical search omitted permission filter: %#v", searchBody)
	}
}

func TestPermissionFilterAllowsOwnerOrganizationAndPublicOnly(t *testing.T) {
	want := map[string]any{
		"bool": map[string]any{
			"should": []any{
				map[string]any{"term": map[string]any{"user_id": uint(7)}},
				map[string]any{"term": map[string]any{"is_public": true}},
				map[string]any{"terms": map[string]any{"org_tag": []string{"finance"}}},
			},
			"minimum_should_match": 1,
		},
	}
	if got := buildPermissionFilter(7, []string{"finance"}); !reflect.DeepEqual(got, want) {
		t.Fatalf("permission filter = %#v, want %#v", got, want)
	}
}
