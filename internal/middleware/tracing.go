package middleware

import (
	"strings"

	"github.com/gin-gonic/gin"
	"github.com/huadeng408/RAG-High-Availability/pkg/observability"
)

// TraceContext establishes one trace identity at the HTTP/WebSocket edge.
func TraceContext() gin.HandlerFunc {
	return func(c *gin.Context) {
		ctx := observability.WithTraceID(c.Request.Context(), strings.TrimSpace(c.GetHeader("X-Trace-ID")))
		traceID := observability.TraceID(ctx)
		c.Request = c.Request.WithContext(ctx)
		c.Set("traceId", traceID)
		c.Header("X-Trace-ID", traceID)
		c.Next()
	}
}
