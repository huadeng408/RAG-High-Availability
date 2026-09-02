// Package database contains shared database clients.
package database

import "fmt"

// EnsureRuntimeSchema applies lightweight runtime fixes for columns that must
// stay compatible with newer local model names and runtime configuration.
func EnsureRuntimeSchema() error {
	if DB == nil {
		return fmt.Errorf("database is not initialized")
	}

	statements := []string{
		"ALTER TABLE document_vectors MODIFY COLUMN model_version VARCHAR(128) NULL",
	}

	for _, stmt := range statements {
		if err := DB.Exec(stmt).Error; err != nil {
			return err
		}
	}

	return ensureImageMetadataColumns()
}

func ensureImageMetadataColumns() error {
	columns := []struct {
		table string
		name  string
	}{
		{table: "document_vectors", name: "image_metadata"},
		{table: "evidence_units", name: "image_metadata"},
	}
	for _, column := range columns {
		if !DB.Migrator().HasTable(column.table) || DB.Migrator().HasColumn(column.table, column.name) {
			continue
		}
		if err := DB.Exec(fmt.Sprintf("ALTER TABLE %s ADD COLUMN %s JSON NULL", column.table, column.name)).Error; err != nil {
			return fmt.Errorf("add %s.%s: %w", column.table, column.name, err)
		}
	}
	return nil
}
