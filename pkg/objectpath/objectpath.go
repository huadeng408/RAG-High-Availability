// Package objectpath contains object-storage path helpers.
package objectpath

import (
	"fmt"
	"path"
)

// MergedObjectName returns the canonical MinIO object path for a merged file.
func MergedObjectName(fileMD5, fileName string) string {
	baseName := path.Base(fileName)
	if baseName == "." || baseName == "/" || baseName == "" {
		return fmt.Sprintf("merged/%s", fileMD5)
	}
	return fmt.Sprintf("merged/%s/%s", fileMD5, baseName)
}
