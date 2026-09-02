package service

import "testing"

func TestUploadFileTypesIncludeSupportedImages(t *testing.T) {
	service := &uploadService{}
	supported, err := service.GetSupportedFileTypes()
	if err != nil {
		t.Fatal(err)
	}
	extensions, ok := supported["supportedExtensions"].([]string)
	if !ok {
		t.Fatalf("supportedExtensions has unexpected type: %T", supported["supportedExtensions"])
	}
	extensionSet := make(map[string]struct{}, len(extensions))
	for _, extension := range extensions {
		extensionSet[extension] = struct{}{}
	}

	for _, fileName := range []string{"inspection.png", "inspection.jpg", "inspection.jpeg"} {
		extension := fileName[len("inspection"):]
		if _, exists := extensionSet[extension]; !exists {
			t.Errorf("%s is missing from supported upload extensions", extension)
		}
		if got := getFileType(fileName); got != "IMAGE" {
			t.Errorf("getFileType(%q) = %q, want IMAGE", fileName, got)
		}
	}
}
