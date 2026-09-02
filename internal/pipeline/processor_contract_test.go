package pipeline

import (
	"context"
	"errors"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	"github.com/huadeng408/RAG-High-Availability/internal/config"
	"github.com/huadeng408/RAG-High-Availability/internal/model"
	"github.com/huadeng408/RAG-High-Availability/pkg/log"
	orchestratorclient "github.com/huadeng408/RAG-High-Availability/pkg/orchestrator"
	"github.com/huadeng408/RAG-High-Availability/pkg/storage"
	"github.com/huadeng408/RAG-High-Availability/pkg/tasks"
	"github.com/minio/minio-go/v7"
)

type parseURLIngestionStub struct {
	objectURL string
}

func (s *parseURLIngestionStub) Enabled() bool { return true }
func (s *parseURLIngestionStub) Parse(_ context.Context, _ tasks.FileProcessingTask, objectURL string) (model.ParsedDocument, error) {
	s.objectURL = objectURL
	return model.ParsedDocument{}, errors.New("stop after URL capture")
}
func (*parseURLIngestionStub) Chunk(context.Context, tasks.FileProcessingTask, model.ParsedDocument, int, int) ([]model.StructuredChunk, error) {
	return nil, nil
}
func (*parseURLIngestionStub) Embed(context.Context, tasks.FileProcessingTask, []string) ([][]float32, error) {
	return nil, nil
}
func (*parseURLIngestionStub) Index(context.Context, tasks.FileProcessingTask, string, []model.EsDocument) (int, error) {
	return 0, nil
}

var _ orchestratorclient.IngestionClient = (*parseURLIngestionStub)(nil)

func TestProcessParseExternalRegeneratesPresignedURLFromObjectIdentity(t *testing.T) {
	log.Init("info", "console", "")
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Query().Has("location") {
			w.Header().Set("Content-Type", "application/xml")
			_, _ = w.Write([]byte(`<LocationConstraint xmlns="http://s3.amazonaws.com/doc/2006-03-01/"></LocationConstraint>`))
			return
		}
		w.WriteHeader(http.StatusOK)
	}))
	t.Cleanup(server.Close)
	client, err := minio.New(strings.TrimPrefix(server.URL, "http://"), &minio.Options{Creds: nil, Secure: false})
	if err != nil {
		t.Fatal(err)
	}
	previous := storage.MinioClient
	storage.MinioClient = client
	t.Cleanup(func() { storage.MinioClient = previous })

	ingestion := &parseURLIngestionStub{}
	processor := &Processor{
		minioCfg:        config.MinIOConfig{BucketName: "rha"},
		ingestionClient: ingestion,
	}
	task := tasks.FileProcessingTask{
		FileMD5: "0123456789abcdef0123456789abcdef", FileName: "recovery.pdf",
		DocumentVersion: "version-sha", ObjectURL: "https://stale.example/object?X-Amz-Expires=1",
		Stage: tasks.StageParse,
	}
	if err := processor.Process(context.Background(), task); err == nil {
		t.Fatal("expected ingestion stub error")
	}
	if ingestion.objectURL == task.ObjectURL || !strings.Contains(ingestion.objectURL, "merged/") {
		t.Fatalf("object URL = %q, want fresh URL for merged object", ingestion.objectURL)
	}
}

func TestProcessParsePdfFailsClosedWhenStructuredIngestionDisabled(t *testing.T) {
	processor := &Processor{}
	err := processor.Process(context.Background(), tasks.FileProcessingTask{FileName: "report.pdf", Stage: tasks.StageParse})
	if err == nil || !strings.Contains(strings.ToLower(err.Error()), "structured ingestion") {
		t.Fatalf("err=%v, want structured ingestion requirement", err)
	}
}
