package protocol

import (
	"encoding/json"
	"os"
)

// PatchSessionHeaders updates only headers in session.json (preserves all other fields).
func PatchSessionHeaders(root string, headers map[string]string) error {
	if len(headers) == 0 {
		return nil
	}
	path := SessionFilePath(root)
	raw, err := os.ReadFile(path)
	if err != nil {
		return err
	}
	var doc map[string]json.RawMessage
	if err := json.Unmarshal(raw, &doc); err != nil {
		return err
	}
	var existing map[string]string
	if h, ok := doc["headers"]; ok {
		_ = json.Unmarshal(h, &existing)
	}
	if existing == nil {
		existing = map[string]string{}
	}
	for k, v := range headers {
		if v != "" {
			existing[k] = v
		}
	}
	hb, err := json.Marshal(existing)
	if err != nil {
		return err
	}
	doc["headers"] = hb
	out, err := json.MarshalIndent(doc, "", "  ")
	if err != nil {
		return err
	}
	return os.WriteFile(path, out, 0o644)
}
