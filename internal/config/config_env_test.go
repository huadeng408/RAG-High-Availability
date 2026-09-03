package config

import (
	"os"
	"testing"
)

func TestExpandEnvironmentPlaceholdersRemovesTrackedCredentialDefaults(t *testing.T) {
	t.Setenv("RHA_TEST_PASSWORD", "runtime-secret")
	cfg := Config{}
	cfg.Database.Redis.Password = "${RHA_TEST_PASSWORD}"
	cfg.JWT.Secret = "${RHA_TEST_PASSWORD}"
	expandEnvironmentPlaceholders(&cfg)
	if cfg.Database.Redis.Password != "runtime-secret" || cfg.JWT.Secret != "runtime-secret" {
		t.Fatalf("environment placeholders were not expanded: %#v", cfg)
	}
	os.Unsetenv("RHA_TEST_PASSWORD")
}

func TestNormalizeMemoryConfigAppliesIndexDefaultsBeforeStartup(t *testing.T) {
	got := NormalizeMemoryConfig(MemoryConfig{Enabled: true})
	if got.MemoryIndexName != "conversation_memory" {
		t.Fatalf("memory index name = %q, want conversation_memory", got.MemoryIndexName)
	}
	if got.LongTermTopK != 4 || got.ContextTopK != 6 || got.LongTermMinImportance != 0.45 {
		t.Fatalf("memory retrieval defaults were not applied: %#v", got)
	}

	explicit := NormalizeMemoryConfig(MemoryConfig{MemoryIndexName: "tenant-memory"})
	if explicit.MemoryIndexName != "tenant-memory" {
		t.Fatalf("explicit memory index name changed to %q", explicit.MemoryIndexName)
	}
}
