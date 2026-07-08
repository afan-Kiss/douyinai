package protocol

import (
	"fmt"
	"io"
	"net/http"
	"strings"
	"time"
)

// RefreshCSRF performs HEAD fetch for x-secsdk-csrf-token (pure HTTP, no Python).
func RefreshCSRF(s *Session) (string, error) {
	passport := s.Cookies["passport_csrf_token"]
	csrfSess := s.Cookies["csrf_session_id"]
	if passport == "" || csrfSess == "" {
		return "", fmt.Errorf("missing csrf cookies")
	}

	url := PigeonHost + "/chat/api/backstage/conversation/get_link_info?biz_type=4&PIGEON_BIZ_TYPE=2&_pms=1&device_platform=web&FUSION=true"
	req, err := http.NewRequest(http.MethodHead, url, nil)
	if err != nil {
		return "", err
	}
	req.Header.Set("User-Agent", s.UserAgent)
	req.Header.Set("Referer", s.WorkspaceReferer())
	req.Header.Set("Origin", IMHost)
	req.Header.Set("Accept", "*/*")
	req.Header.Set("Cookie", s.CookieHeader())

	client := &http.Client{Timeout: 15 * time.Second}
	resp, err := client.Do(req)
	if err != nil {
		return "", err
	}
	defer resp.Body.Close()
	_, _ = io.Copy(io.Discard, resp.Body)

	if resp.StatusCode >= 400 {
		passport := s.Cookies["passport_csrf_token"]
		csrfSess := s.Cookies["csrf_session_id"]
		if passport != "" && csrfSess != "" {
			return fmt.Sprintf("000100000001%s,%s", passport, csrfSess), nil
		}
		return "", fmt.Errorf("csrf HEAD status %d", resp.StatusCode)
	}

	token := resp.Header.Get("x-ware-csrf-token")
	if token == "" {
		token = resp.Header.Get("X-Ware-Csrf-Token")
	}
	if token == "" {
		// secsdk format from cookies
		token = fmt.Sprintf("000100000001%s,%s", passport, csrfSess)
	} else if !strings.Contains(token, ",") {
		token = fmt.Sprintf("000100000001%s,%s", passport, csrfSess)
	}

	s.Headers["x-secsdk-csrf-token"] = token
	return token, nil
}
