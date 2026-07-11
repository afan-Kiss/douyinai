package protocol

import (
	"encoding/json"
	"os"
	"path/filepath"
)

type registryDoc struct {
	ActiveAccountID string `json:"active_account_id"`
}

func ActiveAccountID(root string) string {
	data, err := os.ReadFile(filepath.Join(root, "accounts", "registry.json"))
	if err == nil {
		var doc registryDoc
		if json.Unmarshal(data, &doc) == nil && doc.ActiveAccountID != "" {
			return doc.ActiveAccountID
		}
	}
	if v := os.Getenv("PIGEON_ACCOUNT_ID"); v != "" {
		return v
	}
	return ""
}

func SessionFilePath(root string) string {
	if v := os.Getenv("PIGEON_SESSION_DIR"); v != "" {
		return filepath.Join(v, "session.json")
	}
	if aid := ActiveAccountID(root); aid != "" {
		return filepath.Join(root, "accounts", aid, "session.json")
	}
	return filepath.Join(root, "session", "session.json")
}

func QRImagePath(root string) string {
	if v := os.Getenv("PIGEON_LOGS_DIR"); v != "" {
		return filepath.Join(v, "fxg_login_qr.png")
	}
	if aid := ActiveAccountID(root); aid != "" {
		return filepath.Join(root, "accounts", aid, "logs", "fxg_login_qr.png")
	}
	legacy := filepath.Join(root, "logs", "fxg_login_qr.png")
	if _, err := os.Stat(legacy); err == nil {
		return legacy
	}
	return filepath.Join(root, "session", "logs", "fxg_login_qr.png")
}

func SessionPackDefaultPath(root string) string {
	return filepath.Join(filepath.Dir(SessionFilePath(root)), "pigeon_session_pack.zip")
}
