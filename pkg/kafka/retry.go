package kafka

import "time"

const maxRetryBackoff = 5 * time.Second

func retryDelay(base time.Duration, retryCount int) time.Duration {
	if base <= 0 {
		base = 800 * time.Millisecond
	}
	if retryCount <= 1 {
		return base
	}
	delay := base
	for attempt := 1; attempt < retryCount; attempt++ {
		if delay >= maxRetryBackoff/2 {
			return maxRetryBackoff
		}
		delay *= 2
	}
	if delay > maxRetryBackoff {
		return maxRetryBackoff
	}
	return delay
}
