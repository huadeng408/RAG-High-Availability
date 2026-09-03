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
