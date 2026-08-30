package tasks

import (
	"encoding/json"
	"testing"
)

func TestFileProcessingTaskSerializesVersionedIdentity(t *testing.T) {
	task := FileProcessingTask{
		FileMD5:         "upload-checksum",
		DocumentVersion: "version-sha256",
		WindowID:        "0002",
		TraceID:         "trace-123",
	}

	payload, err := json.Marshal(task)
	if err != nil {
		t.Fatal(err)
	}

	var decoded map[string]json.RawMessage
	if err := json.Unmarshal(payload, &decoded); err != nil {
		t.Fatal(err)
	}
	var fileMD5, documentVersion, windowID, traceID string
	for field, target := range map[string]*string{
		"file_md5":         &fileMD5,
		"document_version": &documentVersion,
		"window_id":        &windowID,
		"trace_id":         &traceID,
	} {
		if err := json.Unmarshal(decoded[field], target); err != nil {
			t.Fatalf("decode %s: %v", field, err)
		}
	}
	if fileMD5 != "upload-checksum" || documentVersion != "version-sha256" {
		t.Fatalf("upload checksum or version identity missing: %s", payload)
	}
	if windowID != "0002" || traceID != "trace-123" {
		t.Fatalf("window or trace identity missing: %s", payload)
	}
}
