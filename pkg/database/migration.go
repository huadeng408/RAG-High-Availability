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

	if err := ensureImageMetadataColumns(); err != nil {
		return err
	}
	return ensurePipelineTaskSchema()
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

func ensurePipelineTaskSchema() error {
	const table = "pipeline_task"
	if !DB.Migrator().HasTable(table) {
		return nil
	}
	columns := []struct {
		name       string
		definition string
	}{
		{name: "document_version", definition: "VARCHAR(96) NULL"},
		{name: "window_id", definition: "VARCHAR(64) NULL"},
		{name: "retry_count", definition: "INT NOT NULL DEFAULT 0"},
		{name: "error_class", definition: "VARCHAR(32) NULL"},
		{name: "last_trace_id", definition: "VARCHAR(128) NULL"},
		{name: "task_payload", definition: "LONGTEXT NULL"},
		{name: "attempt_count", definition: "INT NOT NULL DEFAULT 0"},
		{name: "publication_status", definition: "VARCHAR(20) NOT NULL DEFAULT ''"},
		{name: "publication_attempt_count", definition: "INT NOT NULL DEFAULT 0"},
		{name: "publication_claimed_at", definition: "TIMESTAMP NULL"},
		{name: "published_at", definition: "TIMESTAMP NULL"},
		{name: "publication_last_error", definition: "TEXT NULL"},
	}
	for _, column := range columns {
		if DB.Migrator().HasColumn(table, column.name) {
			continue
		}
		if err := DB.Exec(fmt.Sprintf("ALTER TABLE %s ADD COLUMN %s %s", table, column.name, column.definition)).Error; err != nil {
			return fmt.Errorf("add %s.%s: %w", table, column.name, err)
		}
	}

	documentVersionExpression := "'upload:' || file_md5"
	chunkWindowExpression := "CAST(chunk_id AS TEXT)"
	if DB.Dialector.Name() == "mysql" {
		documentVersionExpression = "CONCAT('upload:', file_md5)"
		chunkWindowExpression = "CAST(chunk_id AS CHAR)"
	}
	if err := DB.Exec(
		"UPDATE pipeline_task SET document_version = " + documentVersionExpression + " WHERE document_version IS NULL OR document_version = ''",
	).Error; err != nil {
		return fmt.Errorf("backfill pipeline document version: %w", err)
	}
	if err := DB.Exec(
		"UPDATE pipeline_task SET window_id = CASE WHEN chunk_id >= 0 THEN " + chunkWindowExpression + " ELSE 'root' END WHERE window_id IS NULL OR window_id = ''",
	).Error; err != nil {
		return fmt.Errorf("backfill pipeline window: %w", err)
	}
	if err := DB.Exec(
		"UPDATE pipeline_task SET attempt_count = retry_count + CASE WHEN status = 'PENDING' THEN 0 ELSE 1 END WHERE attempt_count = 0",
	).Error; err != nil {
		return fmt.Errorf("backfill pipeline attempt count: %w", err)
	}
	if err := DB.Exec(
		"UPDATE pipeline_task SET publication_status = 'PENDING' WHERE task_payload IS NOT NULL AND task_payload <> '' AND publication_status = ''",
	).Error; err != nil {
		return fmt.Errorf("backfill pipeline publication status: %w", err)
	}

	if DB.Migrator().HasIndex(table, "uk_pipeline_file_stage_chunk") {
		if err := DB.Migrator().DropIndex(table, "uk_pipeline_file_stage_chunk"); err != nil {
			return fmt.Errorf("drop legacy pipeline identity index: %w", err)
		}
	}
	if !DB.Migrator().HasIndex(table, "idx_document_version_stage_window") {
		if err := DB.Exec(
			"CREATE UNIQUE INDEX idx_document_version_stage_window ON pipeline_task(document_version, stage, window_id)",
		).Error; err != nil {
			return fmt.Errorf("create versioned pipeline identity index: %w", err)
		}
	}
	if DB.Dialector.Name() == "mysql" {
		if err := DB.Exec(
			"ALTER TABLE pipeline_task MODIFY COLUMN document_version VARCHAR(96) NOT NULL, MODIFY COLUMN window_id VARCHAR(64) NOT NULL, MODIFY COLUMN idempotency_key VARCHAR(255) NOT NULL",
		).Error; err != nil {
			return fmt.Errorf("enforce versioned pipeline identity columns: %w", err)
		}
	}
	return nil
}
