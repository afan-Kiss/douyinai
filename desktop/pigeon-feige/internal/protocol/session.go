package protocol

import (
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
)

const (
	PigeonHost = "https://pigeon.jinritemai.com"
	IMHost     = "https://im.jinritemai.com"
)

type Session struct {
	Cookies      map[string]string `json:"cookies"`
	Headers      map[string]string `json:"headers"`
	QueryTokens  map[string]string `json:"query_tokens"`
	WSUrls       []string          `json:"ws_urls"`
	DeviceID     string            `json:"device_id"`
	ShopID       string            `json:"shop_id"`
	UserAgent    string            `json:"user_agent"`
}

func LoadSession(root string) (*Session, error) {
	path := SessionFilePath(root)
	data, err := os.ReadFile(path)
	if err != nil {
		return &Session{Cookies: map[string]string{}, Headers: map[string]string{}, QueryTokens: map[string]string{}}, nil
	}
	var s Session
	if err := json.Unmarshal(data, &s); err != nil {
		return &Session{Cookies: map[string]string{}, Headers: map[string]string{}, QueryTokens: map[string]string{}}, nil
	}
	if s.Cookies == nil {
		s.Cookies = map[string]string{}
	}
	if s.Headers == nil {
		s.Headers = map[string]string{}
	}
	if s.QueryTokens == nil {
		s.QueryTokens = map[string]string{}
	}
	if s.UserAgent == "" {
		s.UserAgent = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/132.0.0.0 Safari/537.36"
	}
	return &s, nil
}

func (s *Session) CookieHeader() string {
	parts := make([]string, 0, len(s.Cookies))
	for k, v := range s.Cookies {
		if k != "" && v != "" {
			parts = append(parts, k+"="+v)
		}
	}
	return strings.Join(parts, "; ")
}

func (s *Session) LoggedIn() bool {
	return s.Cookies["sessionid"] != "" || s.Cookies["sid_tt"] != ""
}

func (s *Session) ShopLabel() string {
	shop := s.Cookies["SHOP_ID"]
	if shop == "" {
		shop = s.ShopID
	}
	if shop == "" {
		return "飞鸽客服"
	}
	return "店铺 " + shop
}

func SaveSession(root string, s *Session) error {
	path := SessionFilePath(root)
	data, err := json.MarshalIndent(s, "", "  ")
	if err != nil {
		return err
	}
	if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
		return err
	}
	return os.WriteFile(path, data, 0o644)
}

func (s *Session) WorkspaceReferer() string {
	cid := s.Cookies["PIGEON_CID"]
	if cid == "" {
		cid = s.DeviceID
	}
	base := IMHost + "/pc_seller_v2/main/workspace"
	if cid != "" {
		return base + "?selfId=" + cid
	}
	return base
}
