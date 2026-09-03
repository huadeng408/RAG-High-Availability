package handler

import (
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	"github.com/gin-gonic/gin"
	"github.com/huadeng408/RAG-High-Availability/internal/service"
	"github.com/huadeng408/RAG-High-Availability/pkg/tasks"
)

type replayAdminServiceStub struct {
	service.AdminService
	called bool
	err    error
}

func (s *replayAdminServiceStub) ReplayPipelineTask(fileMD5, documentVersion string, stage tasks.Stage, windowID, dlqMessageID string) (*service.PipelineReplayResult, error) {
	s.called = true
	if s.err != nil {
		return nil, s.err
	}
	return &service.PipelineReplayResult{FileMD5: fileMD5, DocumentVersion: documentVersion, Stage: stage, WindowID: windowID, DLQMessageID: dlqMessageID, ReplayedTasks: 1, MessageIDs: []string{dlqMessageID}}, nil
}

func TestReplayPipelineTaskMapsTypedFailuresToDistinctHTTPClasses(t *testing.T) {
	tests := []struct {
		name string
		kind service.PipelineReplayErrorKind
		want int
	}{
		{name: "validation", kind: service.PipelineReplayValidation, want: http.StatusBadRequest},
		{name: "not found", kind: service.PipelineReplayNotFound, want: http.StatusNotFound},
		{name: "conflict", kind: service.PipelineReplayConflict, want: http.StatusConflict},
		{name: "infrastructure", kind: service.PipelineReplayInfrastructure, want: http.StatusServiceUnavailable},
	}
	for _, tc := range tests {
		t.Run(tc.name, func(t *testing.T) {
			gin.SetMode(gin.TestMode)
			stub := &replayAdminServiceStub{err: service.NewPipelineReplayError(tc.kind, "classified failure", nil)}
			router := gin.New()
			router.POST("/replay", NewAdminHandler(stub, nil).ReplayPipelineTask)
			body := `{"fileMd5":"file","documentVersion":"version","stage":"embed","windowId":"window-2","dlqMessageId":"` + strings.Repeat("a", 64) + `"}`
			req := httptest.NewRequest(http.MethodPost, "/replay", strings.NewReader(body))
			req.Header.Set("Content-Type", "application/json")
			resp := httptest.NewRecorder()
			router.ServeHTTP(resp, req)
			if resp.Code != tc.want {
				t.Fatalf("status=%d want=%d body=%s", resp.Code, tc.want, resp.Body.String())
			}
		})
	}
}

func TestReplayPipelineTaskRequiresDurableIdentityFields(t *testing.T) {
	gin.SetMode(gin.TestMode)
	stub := &replayAdminServiceStub{}
	router := gin.New()
	router.POST("/replay", NewAdminHandler(stub, nil).ReplayPipelineTask)
	req := httptest.NewRequest(http.MethodPost, "/replay", strings.NewReader(`{"fileMd5":"file"}`))
	req.Header.Set("Content-Type", "application/json")
	resp := httptest.NewRecorder()
	router.ServeHTTP(resp, req)
	if resp.Code != http.StatusBadRequest || stub.called {
		t.Fatalf("status=%d called=%v body=%s", resp.Code, stub.called, resp.Body.String())
	}
}

func TestReplayPipelineTaskPassesExactDurableIdentity(t *testing.T) {
	gin.SetMode(gin.TestMode)
	stub := &replayAdminServiceStub{}
	router := gin.New()
	router.POST("/replay", NewAdminHandler(stub, nil).ReplayPipelineTask)
	body := `{"fileMd5":"file","documentVersion":"version","stage":"embed","windowId":"window-2","dlqMessageId":"` + strings.Repeat("a", 64) + `"}`
	req := httptest.NewRequest(http.MethodPost, "/replay", strings.NewReader(body))
	req.Header.Set("Content-Type", "application/json")
	resp := httptest.NewRecorder()
	router.ServeHTTP(resp, req)
	if resp.Code != http.StatusOK || !stub.called {
		t.Fatalf("status=%d called=%v body=%s", resp.Code, stub.called, resp.Body.String())
	}
}
