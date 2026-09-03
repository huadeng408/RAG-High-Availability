package middleware

import (
	"net/http"
	"net/http/httptest"
	"testing"

	"github.com/gin-gonic/gin"
	"github.com/huadeng408/RAG-High-Availability/internal/config"
	"github.com/huadeng408/RAG-High-Availability/pkg/observability"
)

func TestTraceContextRejectsCallerTraceAtPublicEdge(t *testing.T) {
	gin.SetMode(gin.TestMode)
	router := gin.New()
	router.Use(TraceContext())
	router.GET("/trace", func(c *gin.Context) {
		c.String(http.StatusOK, observability.TraceID(c.Request.Context()))
	})

	request := httptest.NewRequest(http.MethodGet, "/trace", nil)
	request.Header.Set("X-Trace-ID", "trace-123")
	response := httptest.NewRecorder()
	router.ServeHTTP(response, request)

	if response.Code != http.StatusOK {
		t.Fatalf("status = %d, want 200", response.Code)
	}
	if response.Body.String() == "trace-123" {
		t.Fatal("public caller supplied trace was trusted")
	}
	if response.Header().Get("X-Trace-ID") != response.Body.String() {
		t.Fatalf("response trace differs from generated context trace")
	}
}

func TestTraceContextPreservesValidatedInternalTrace(t *testing.T) {
	gin.SetMode(gin.TestMode)
	previous := config.Conf.AI.Orchestrator.SharedSecret
	config.Conf.AI.Orchestrator.SharedSecret = "internal-secret"
	t.Cleanup(func() { config.Conf.AI.Orchestrator.SharedSecret = previous })
	router := gin.New()
	router.Use(TraceContext())
	router.GET("/trace", func(c *gin.Context) { c.String(http.StatusOK, observability.TraceID(c.Request.Context())) })
	request := httptest.NewRequest(http.MethodGet, "/trace", nil)
	request.Header.Set("X-Internal-Token", "internal-secret")
	request.Header.Set("X-Trace-ID", "trace-internal-123")
	response := httptest.NewRecorder()
	router.ServeHTTP(response, request)
	if response.Body.String() != "trace-internal-123" {
		t.Fatalf("internal trace = %q", response.Body.String())
	}
}

func TestTraceContextGeneratesTraceWhenHeaderIsMissing(t *testing.T) {
	gin.SetMode(gin.TestMode)
	router := gin.New()
	router.Use(TraceContext())
	router.GET("/trace", func(c *gin.Context) {
		c.String(http.StatusOK, observability.TraceID(c.Request.Context()))
	})

	response := httptest.NewRecorder()
	router.ServeHTTP(response, httptest.NewRequest(http.MethodGet, "/trace", nil))

	if response.Header().Get("X-Trace-ID") == "" {
		t.Fatal("response trace id is empty")
	}
	if response.Body.String() != response.Header().Get("X-Trace-ID") {
		t.Fatalf("context trace %q differs from response trace %q", response.Body.String(), response.Header().Get("X-Trace-ID"))
	}
}
