package model

import (
	"sync"
	"testing"

	"gorm.io/gorm/schema"
)

func TestDocumentVersionSchemaUsesIndexedVarcharLengths(t *testing.T) {
	parsed, err := schema.Parse(&DocumentVersion{}, &sync.Map{}, schema.NamingStrategy{})
	if err != nil {
		t.Fatalf("parse document version schema: %v", err)
	}

	assertFieldType := func(name string, want string) {
		t.Helper()
		field, ok := parsed.FieldsByName[name]
		if !ok {
			t.Fatalf("field %s missing from schema", name)
		}
		if got := field.TagSettings["TYPE"]; got != want {
			t.Fatalf("field %s type = %q, want %q", name, got, want)
		}
	}

	assertFieldType("DocumentVersionID", "varchar(96)")
	assertFieldType("SourceID", "varchar(96)")
	assertFieldType("ContentSHA256", "char(64)")
}
