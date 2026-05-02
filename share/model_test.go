package share

import "testing"

func TestShareResponseMarksPDFFileShareNativeViewerLinks(t *testing.T) {
	t.Parallel()

	share := Share{
		ID:         "share-pdf",
		SourcePath: "/tmp/sample.pdf",
	}

	got := share.ToResponse("https://host.example.ts.net/share", "token123").URL
	want := "https://host.example.ts.net/share/s/share-pdf?t=token123&pv=native"
	if got != want {
		t.Fatalf("ToResponse().URL = %q, want %q", got, want)
	}
}
