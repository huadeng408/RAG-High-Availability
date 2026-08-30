package es

import (
	"bytes"
	"testing"
)

func TestAliasSwitchUsesAtomicRemoveAndAddActions(t *testing.T) {
	body, err := aliasSwitchBody("rha-knowledge-active", "rha-knowledge-v2")
	if err != nil {
		t.Fatal(err)
	}
	if !bytes.Contains(body, []byte(`"remove"`)) || !bytes.Contains(body, []byte(`"add"`)) {
		t.Fatalf("missing atomic alias actions: %s", body)
	}
	if !bytes.Contains(body, []byte(`"rha-knowledge-active"`)) || !bytes.Contains(body, []byte(`"rha-knowledge-v2"`)) {
		t.Fatalf("missing alias target: %s", body)
	}
}
