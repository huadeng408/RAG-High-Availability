package service

import "context"

type searchContextKey string

const disableRerankContextKey searchContextKey = "disable_rerank"

func WithRerankDisabled(ctx context.Context) context.Context {
	return context.WithValue(ctx, disableRerankContextKey, true)
}

func rerankDisabled(ctx context.Context) bool {
	disabled, ok := ctx.Value(disableRerankContextKey).(bool)
	return ok && disabled
}
