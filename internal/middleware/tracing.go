package middleware

import (
	"crypto/subtle"
	"regexp"
	"strings"

	"github.com/gin-gonic/gin"
	"github.com/huadeng408/RAG-High-Availability/internal/config"
	"github.com/huadeng408/RAG-High-Availability/pkg/observability"
)

var trustedTraceID = regexp.MustCompile(`^[A-Za-z0-9][A-Za-z0-9._:-]{7,127}$`)

// TraceContext establishes one trace identity at the HTTP/WebSocket edge.
func TraceContext() gin.HandlerFunc {
	return func(c *gin.Context) {
		traceID := ""
		candidate := strings.TrimSpace(c.GetHeader("X-Trace-ID"))
		expected := strings.TrimSpace(config.Conf.AI.Orchestrator.SharedSecret)
		provided := c.GetHeader("X-Internal-Token")
		if expected != "" && len(provided) == len(expected) && subtle.ConstantTimeCompare([]byte(provided), []byte(expected)) == 1 && trustedTraceID.MatchString(candidate) {
			traceID = candidate
		}
		ctx := observability.WithTraceID(c.Request.Context(), traceID)
		traceID = observability.TraceID(ctx)
		c.Request = c.Request.WithContext(ctx)
		c.Set("traceId", traceID)
		c.Header("X-Trace-ID", traceID)
		c.Next()
	}
}
