package kafka

import (
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"strconv"
	"time"

	"github.com/huadeng408/RAG-High-Availability/pkg/tasks"
)

const maxRetryBackoff = 5 * time.Second

type deadLetterTracker interface {
	MarkDeadLetterByKey(fileMD5, documentVersion, stage, windowID, lastError, payload, messageID string) error
}

type deadLetterLookup interface {
	GetDeadLetterByKey(fileMD5, documentVersion, stage, windowID string) (payload, messageID string, err error)
}

type taskPublisher func(tasks.FileProcessingTask) error

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

func buildDeadLetterTask(
	task tasks.FileProcessingTask,
	documentVersion, windowID string,
	attempt int,
	lastError string,
) (tasks.FileProcessingTask, []byte, error) {
	task.DocumentVersion = documentVersion
	task.WindowID = windowID
	task.Attempt = attempt
	task.LastError = lastError
	task.DLQID = ""
	identity := documentVersion + "\x00" + string(task.Stage) + "\x00" + windowID + "\x00" + strconv.Itoa(attempt)
	digest := sha256.Sum256([]byte(identity))
	task.DLQID = hex.EncodeToString(digest[:])
	payload, err := json.Marshal(task)
	return task, payload, err
}

func handoffFailedTask(
	tracker deadLetterTracker,
	task tasks.FileProcessingTask,
	documentVersion, windowID string,
	retryCount, maxRetries int,
	processingErr error,
	retryPublisher, dlqPublisher taskPublisher,
) error {
	if processingErr == nil {
		return nil
	}
	if retryCount <= maxRetries {
		task.DocumentVersion = documentVersion
		task.WindowID = windowID
		task.Attempt = retryCount
		task.LastError = processingErr.Error()
		task.DLQID = ""
		return retryPublisher(task)
	}
	if lookup, ok := tracker.(deadLetterLookup); ok {
		storedPayload, storedMessageID, lookupErr := lookup.GetDeadLetterByKey(
			task.FileMD5, documentVersion, string(task.Stage), windowID,
		)
		if lookupErr != nil {
			return lookupErr
		}
		if storedPayload != "" && storedMessageID != "" {
			var storedTask tasks.FileProcessingTask
			if err := json.Unmarshal([]byte(storedPayload), &storedTask); err != nil {
				return err
			}
			if storedTask.FileMD5 != task.FileMD5 || storedTask.DocumentVersion != documentVersion || storedTask.Stage != task.Stage || storedTask.WindowID != windowID {
				return fmt.Errorf("persisted DLQ envelope identity does not match task")
			}
			if storedTask.DLQID != "" && storedTask.DLQID != storedMessageID {
				return fmt.Errorf("persisted DLQ envelope message ID does not match durable record")
			}
			if storedTask.DLQID == "" {
				storedTask.DLQID = storedMessageID
			}
			return dlqPublisher(storedTask)
		}
	}

	deadLetter, payload, err := buildDeadLetterTask(
		task,
		documentVersion,
		windowID,
		retryCount,
		processingErr.Error(),
	)
	if err != nil {
		return err
	}
	if err := tracker.MarkDeadLetterByKey(
		deadLetter.FileMD5,
		deadLetter.DocumentVersion,
		string(deadLetter.Stage),
		deadLetter.WindowID,
		deadLetter.LastError,
		string(payload),
		deadLetter.DLQID,
	); err != nil {
		return err
	}
	return dlqPublisher(deadLetter)
}
