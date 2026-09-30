package tools

import "testing"

func TestTextCarriesTheBodyInBothPlaces(t *testing.T) {
	const body = `[{"score":0.84,"payload":{"document":"apiVersion: v1"}}]`

	res := text(body)

	if len(res.Content) != 1 {
		t.Fatalf("got %d content blocks", len(res.Content))
	}
	if res.StructuredContent.Body != body {
		t.Fatalf("structured body is %q", res.StructuredContent.Body)
	}
}
