package middleware

import (
	"net/http"
	"net/http/httptest"
	"testing"

	"github.com/gin-gonic/gin"
	"github.com/huadeng408/RAG-High-Availability/pkg/observability"
)

func TestTraceContextPropagatesRequestTraceAndResponseHeader(t *testing.T) {
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
	if response.Body.String() != "trace-123" {
		t.Fatalf("context trace = %q, want trace-123", response.Body.String())
	}
	if response.Header().Get("X-Trace-ID") != "trace-123" {
		t.Fatalf("response trace = %q, want trace-123", response.Header().Get("X-Trace-ID"))
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
