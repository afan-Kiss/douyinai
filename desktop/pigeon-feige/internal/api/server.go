package api

import (
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"sync"
	"time"

	"pigeon-feige/internal/bridge"
	"pigeon-feige/internal/protocol"
)

type Server struct {
	Root    string
	Bridge  *bridge.Client
	UIDir   string
	mu      sync.Mutex
	unread  map[string]int
}

func NewServer(root string) *Server {
	return &Server{
		Root:   root,
		Bridge: bridge.NewClient(root),
		UIDir:  filepath.Join(root, "desktop", "ui"),
		unread: map[string]int{},
	}
}

func (s *Server) Handler() http.Handler {
	mux := http.NewServeMux()
	mux.HandleFunc("/", s.handleIndex)
	mux.HandleFunc("/static/", s.handleStatic)
	mux.HandleFunc("/api/health", s.handleHealth)
	mux.HandleFunc("/api/session", s.handleSession)
	mux.HandleFunc("/api/accounts", s.handleAccounts)
	mux.HandleFunc("/api/accounts/switch", s.handleAccountsSwitch)
	mux.HandleFunc("/api/accounts/create", s.handleAccountsCreate)
	mux.HandleFunc("/api/conversations", s.handleConversations)
	mux.HandleFunc("/api/context", s.handleContext)
	mux.HandleFunc("/api/orders", s.handleOrders)
	mux.HandleFunc("/api/events", s.handleEvents)
	mux.HandleFunc("/api/listen/status", s.handleListenStatus)
	mux.HandleFunc("/api/send", s.handleSend)
	mux.HandleFunc("/api/listen/start", s.handleListenStart)
	mux.HandleFunc("/api/listen/stop", s.handleListenStop)
	mux.HandleFunc("/api/session/bootstrap", s.handleSessionBootstrap)
	mux.HandleFunc("/api/session/renew", s.handleSessionRenew)
	mux.HandleFunc("/api/session/keepalive", s.handleSessionKeepalive)
	mux.HandleFunc("/api/session/doctor", s.handleSessionDoctor)
	mux.HandleFunc("/api/session-doctor", s.handleSessionDoctor)
	mux.HandleFunc("/api/conversations/ack", s.handleConvAck)
	mux.HandleFunc("/api/qr-login/start", s.handleQRStart)
	mux.HandleFunc("/api/qr-login/status", s.handleQRStatus)
	mux.HandleFunc("/api/qr-login/image", s.handleQRImage)
	mux.HandleFunc("/api/cdp-onboard/start", s.handleCdpOnboardStart)
	mux.HandleFunc("/api/cdp-onboard/status", s.handleCdpOnboardStatus)
	mux.HandleFunc("/api/cdp-warm/start", s.handleCdpWarmStart)
	mux.HandleFunc("/api/cdp-warm/status", s.handleCdpWarmStatus)
	mux.HandleFunc("/api/ai/suggest", s.handleAISuggest)
	mux.HandleFunc("/api/protocol/status", s.handleProtocolStatus)
	mux.HandleFunc("/api/protocol/prepare", s.handleProtocolPrepare)
	mux.HandleFunc("/api/import-har", s.handleImportHar)
	mux.HandleFunc("/api/import-cookies", s.handleImportCookies)
	mux.HandleFunc("/api/session-pack/export", s.handleSessionPackExport)
	mux.HandleFunc("/api/session-pack/import", s.handleSessionPackImport)
	return withCORS(mux)
}

func (s *Server) StartBackgroundPrepare() {
	go warmCSRF(s.Root)
}

func (s *Server) Close() {
	s.Bridge.Close()
}

func warmCSRF(root string) {
	sess, err := protocol.LoadSession(root)
	if err != nil || sess == nil || !sess.LoggedIn() {
		return
	}
	token, err := protocol.RefreshCSRF(sess)
	if err != nil || token == "" {
		return
	}
	_ = protocol.PatchSessionHeaders(root, map[string]string{"x-secsdk-csrf-token": token})
}

func writeJSON(w http.ResponseWriter, code int, body any) {
	w.Header().Set("Content-Type", "application/json; charset=utf-8")
	w.Header().Set("Access-Control-Allow-Origin", "*")
	w.WriteHeader(code)
	_ = json.NewEncoder(w).Encode(body)
}

func readJSON(r *http.Request) map[string]any {
	if r.Body == nil {
		return map[string]any{}
	}
	defer r.Body.Close()
	raw, _ := io.ReadAll(r.Body)
	if len(raw) == 0 {
		return map[string]any{}
	}
	var out map[string]any
	_ = json.Unmarshal(raw, &out)
	if out == nil {
		out = map[string]any{}
	}
	return out
}

func (s *Server) handleIndex(w http.ResponseWriter, r *http.Request) {
	if r.URL.Path != "/" {
		http.NotFound(w, r)
		return
	}
	index := filepath.Join(s.UIDir, "index.html")
	data, err := os.ReadFile(index)
	if err != nil {
		w.WriteHeader(500)
		_, _ = w.Write([]byte("ui missing"))
		return
	}
	w.Header().Set("Content-Type", "text/html; charset=utf-8")
	w.WriteHeader(200)
	_, _ = w.Write(data)
}

func (s *Server) handleStatic(w http.ResponseWriter, r *http.Request) {
	name := strings.TrimPrefix(r.URL.Path, "/static/")
	if strings.Contains(name, "..") {
		http.NotFound(w, r)
		return
	}
	path := filepath.Join(s.UIDir, filepath.Base(name))
	data, err := os.ReadFile(path)
	if err != nil {
		http.NotFound(w, r)
		return
	}
	ctype := "application/octet-stream"
	switch {
	case strings.HasSuffix(name, ".css"):
		ctype = "text/css; charset=utf-8"
	case strings.HasSuffix(name, ".js"):
		ctype = "application/javascript; charset=utf-8"
	case strings.HasSuffix(name, ".png"):
		ctype = "image/png"
	}
	w.Header().Set("Content-Type", ctype)
	w.Header().Set("Cache-Control", "no-cache")
	w.WriteHeader(200)
	_, _ = w.Write(data)
}

func (s *Server) handleHealth(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodGet {
		writeJSON(w, 405, map[string]any{"ok": false})
		return
	}
	out, err := s.Bridge.Call("ping", nil)
	if err != nil {
		sess, _ := protocol.LoadSession(s.Root)
		loggedIn := false
		if sess != nil {
			loggedIn = sess.LoggedIn()
		}
		writeJSON(w, 200, map[string]any{
			"ok": loggedIn,
			"health": map[string]any{
				"logged_in": loggedIn,
				"bridge":    err.Error(),
			},
			"via": "go/native",
		})
		return
	}
	writeJSON(w, 200, map[string]any{
		"ok":    true,
		"health": out,
		"via":   "go/bridge",
	})
}

func (s *Server) handleSession(w http.ResponseWriter, r *http.Request) {
	start := time.Now()
	resp := map[string]any{
		"logged_in": false,
		"shop_name": "飞鸽客服",
		"qr":        map[string]any{"phase": "logged_out", "error": "", "running": false},
		"onboard":   map[string]any{"phase": "idle", "running": false},
	}
	sess, _ := protocol.LoadSession(s.Root)
	if sess != nil {
		resp["shop_id"] = sess.Cookies["SHOP_ID"]
		resp["shop_name"] = sess.ShopLabel()
		resp["cookie_count"] = len(sess.Cookies)
		if sess.LoggedIn() {
			resp["logged_in"] = true
			resp["qr"] = map[string]any{"phase": "logged_in", "error": "", "running": false}
		}
	}
	out, err := s.Bridge.Call("session_status", nil)
	if err == nil && out != nil {
		for k, v := range out {
			resp[k] = v
		}
	} else if err != nil {
		resp["active_account_id"] = protocol.ActiveAccountID(s.Root)
		resp["bridge_degraded"] = true
	}
	if sess != nil && sess.LoggedIn() {
		qrActive := false
		if qr, ok := resp["qr"].(map[string]any); ok {
			phase, _ := qr["phase"].(string)
			running, _ := qr["running"].(bool)
			qrActive = running && (phase == "fetching" || phase == "waiting_scan" || phase == "scanned" || phase == "bootstrapping")
		}
		if !qrActive {
			resp["logged_in"] = true
			resp["qr"] = map[string]any{"phase": "logged_in", "error": "", "running": false}
		}
	}
	if os.Getenv("PIGEON_DEV") == "1" {
		fmt.Fprintf(os.Stderr, "[api] session %.0fms\n", float64(time.Since(start).Milliseconds()))
	}
	writeJSON(w, 200, resp)
}

func (s *Server) handleAccounts(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodGet {
		writeJSON(w, 405, map[string]any{"ok": false})
		return
	}
	out, err := s.Bridge.Call("list_accounts", nil)
	if err != nil {
		writeJSON(w, 500, map[string]any{"ok": false, "error": err.Error()})
		return
	}
	writeJSON(w, 200, out)
}

func (s *Server) handleAccountsSwitch(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		writeJSON(w, 405, map[string]any{"ok": false})
		return
	}
	body := readJSON(r)
	accountID, _ := body["account_id"].(string)
	if accountID == "" {
		accountID, _ = body["id"].(string)
	}
	if accountID == "" {
		writeJSON(w, 400, map[string]any{"ok": false, "error": "account_id required"})
		return
	}
	restartListen := true
	if v, ok := body["restart_listen"].(bool); ok {
		restartListen = v
	}
	out, err := s.Bridge.Call("switch_account", map[string]any{
		"account_id":      accountID,
		"restart_listen":  restartListen,
	})
	if err != nil {
		writeJSON(w, 500, map[string]any{"ok": false, "error": err.Error()})
		return
	}
	writeJSON(w, 200, out)
}

func (s *Server) handleAccountsCreate(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		writeJSON(w, 405, map[string]any{"ok": false})
		return
	}
	body := readJSON(r)
	label, _ := body["label"].(string)
	if label == "" {
		label = "新账号"
	}
	out, err := s.Bridge.Call("create_account", map[string]any{"label": label})
	if err != nil {
		writeJSON(w, 500, map[string]any{"ok": false, "error": err.Error()})
		return
	}
	writeJSON(w, 200, out)
}

func (s *Server) handleConversations(w http.ResponseWriter, r *http.Request) {
	page := 0
	size := 30
	if v := r.URL.Query().Get("page"); v != "" {
		if n, err := strconv.Atoi(v); err == nil {
			page = n
		}
	}
	if v := r.URL.Query().Get("size"); v != "" {
		if n, err := strconv.Atoi(v); err == nil {
			size = n
		}
	}
	warmCSRF(s.Root)
	params := map[string]any{"page": page, "size": size}
	if category := strings.TrimSpace(r.URL.Query().Get("category")); category != "" {
		params["category"] = category
	}
	out, err := s.Bridge.Call("conv_list", params)
	if err != nil || !convListOK(out) {
		_, _ = s.Bridge.Call("session_doctor", map[string]any{"fix": true})
		out, err = s.Bridge.Call("conv_list", params)
	}
	if err != nil || !convListOK(out) {
		_, _ = s.Bridge.Call("refresh_conv_snapshot", map[string]any{"cdp_only": true, "size": size})
		out, err = s.Bridge.Call("conv_list", params)
	}
	if err != nil {
		writeJSON(w, 500, map[string]any{"ok": false, "error": err.Error()})
		return
	}
	s.applyUnreadBumps(out)
	writeJSON(w, 200, map[string]any{
		"ok":    convListOK(out),
		"items": out["items"],
		"raw":   out["raw"],
		"via":   "go/bridge",
	})
}

func convListOK(out map[string]any) bool {
	if out == nil {
		return false
	}
	if v, ok := out["ok"].(bool); ok && v {
		return true
	}
	if items, ok := out["items"].([]any); ok && len(items) > 0 {
		return true
	}
	if n, ok := out["count"].(float64); ok && n > 0 {
		return true
	}
	return false
}

func (s *Server) applyUnreadBumps(out map[string]any) {
	arr, ok := out["items"].([]any)
	if !ok {
		return
	}
	s.mu.Lock()
	bumps := map[string]int{}
	for k, v := range s.unread {
		bumps[k] = v
	}
	s.mu.Unlock()
	for _, it := range arr {
		m, ok := it.(map[string]any)
		if !ok {
			continue
		}
		uid, _ := m["security_user_id"].(string)
		if uid == "" || bumps[uid] <= 0 {
			continue
		}
		if uc, ok := m["unread_count"].(float64); ok {
			m["unread_count"] = int(uc) + bumps[uid]
		} else {
			m["unread_count"] = bumps[uid]
		}
	}
}

func (s *Server) handleContext(w http.ResponseWriter, r *http.Request) {
	uid := r.URL.Query().Get("user_id")
	if uid == "" {
		writeJSON(w, 400, map[string]any{"ok": false, "error": "user_id required"})
		return
	}
	out, err := s.Bridge.Call("context", map[string]any{"user_id": uid})
	if err != nil {
		writeJSON(w, 500, map[string]any{"ok": false, "error": err.Error()})
		return
	}
	s.mu.Lock()
	delete(s.unread, uid)
	s.mu.Unlock()
	ctx, _ := out["context"]
	msgCount := 0
	if mc, ok := out["message_count"].(float64); ok {
		msgCount = int(mc)
	}
	writeJSON(w, 200, map[string]any{"ok": msgCount > 0, "context": ctx})
}

func (s *Server) handleOrders(w http.ResponseWriter, r *http.Request) {
	uid := r.URL.Query().Get("user_id")
	if uid == "" {
		writeJSON(w, 400, map[string]any{"ok": false, "error": "user_id required"})
		return
	}
	out, err := s.Bridge.Call("orders", map[string]any{"user_id": uid})
	if err != nil {
		writeJSON(w, 500, map[string]any{"ok": false, "error": err.Error()})
		return
	}
	ok := false
	if v, _ := out["order_ok"].(bool); v {
		ok = true
	}
	if v, _ := out["has_order"].(bool); v {
		ok = true
	}
	if orders, _ := out["orders"].(map[string]any); orders != nil {
		if cards, _ := orders["cards"].([]any); len(cards) > 0 {
			ok = true
		}
		if v, _ := orders["has_order"].(bool); v {
			ok = true
		}
	}
	resp := map[string]any{"ok": ok, "orders": out["orders"], "source": out["source"], "order_ok": ok}
	if e, _ := out["error"].(string); e != "" {
		resp["error"] = e
	}
	writeJSON(w, 200, resp)
}

func (s *Server) handleEvents(w http.ResponseWriter, r *http.Request) {
	since := 0
	if v := r.URL.Query().Get("since"); v != "" {
		since, _ = strconv.Atoi(v)
	}
	out, err := s.Bridge.Call("events", map[string]any{"since": since})
	if err != nil {
		writeJSON(w, 503, map[string]any{"ok": false, "error": err.Error(), "items": []any{}, "last_seq": since})
		return
	}
	if items, ok := out["items"].([]any); ok {
		for _, it := range items {
			m, ok := it.(map[string]any)
			if !ok || m["kind"] != "message" {
				continue
			}
			msg, _ := m["message"].(map[string]any)
			uid, _ := msg["security_user_id"].(string)
			if uid != "" {
				s.mu.Lock()
				s.unread[uid] = s.unread[uid] + 1
				s.mu.Unlock()
			}
		}
	}
	writeJSON(w, 200, out)
}

func (s *Server) handleListenStatus(w http.ResponseWriter, r *http.Request) {
	out, err := s.Bridge.Call("listen_status", nil)
	if err != nil {
		writeJSON(w, 500, map[string]any{"ok": false, "error": err.Error(), "running": false})
		return
	}
	if out == nil {
		out = map[string]any{"ok": true, "running": false}
	}
	writeJSON(w, 200, out)
}

func (s *Server) handleListenStart(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		writeJSON(w, 405, map[string]any{"ok": false})
		return
	}
	out, err := s.Bridge.Call("listen_start", nil)
	if err != nil {
		writeJSON(w, 500, map[string]any{"ok": false, "error": err.Error()})
		return
	}
	writeJSON(w, 200, out)
}

func (s *Server) handleListenStop(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		writeJSON(w, 405, map[string]any{"ok": false})
		return
	}
	out, err := s.Bridge.Call("listen_stop", nil)
	if err != nil {
		writeJSON(w, 500, map[string]any{"ok": false, "error": err.Error()})
		return
	}
	if out == nil {
		out = map[string]any{"ok": true, "running": false}
	}
	writeJSON(w, 200, out)
}

func (s *Server) handleSend(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		writeJSON(w, 405, map[string]any{"ok": false})
		return
	}
	body := readJSON(r)
	uid, _ := body["user_id"].(string)
	text, _ := body["text"].(string)
	if uid == "" || text == "" {
		writeJSON(w, 400, map[string]any{"ok": false, "error": "user_id and text required"})
		return
	}
	out, err := s.Bridge.Call("send", map[string]any{"user_id": uid, "text": text})
	if err != nil {
		writeJSON(w, 500, map[string]any{"ok": false, "error": err.Error()})
		return
	}
	sent, _ := out["sent"].(bool)
	resp := map[string]any{"ok": sent, "sent": sent}
	for _, k := range []string{
		"result", "reason", "preflight_failed", "send_ready", "recommended_action",
		"needs_cdp_onboard", "blockers", "heal",
	} {
		if v, ok := out[k]; ok {
			resp[k] = v
		}
	}
	writeJSON(w, 200, resp)
}

func (s *Server) handleSessionBootstrap(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		writeJSON(w, 405, map[string]any{"ok": false})
		return
	}
	out, err := s.Bridge.Call("session_bootstrap", readJSON(r))
	if err != nil {
		writeJSON(w, 500, map[string]any{"ok": false, "error": err.Error()})
		return
	}
	writeJSON(w, 200, out)
}

func (s *Server) handleSessionRenew(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		writeJSON(w, 405, map[string]any{"ok": false})
		return
	}
	out, err := s.Bridge.Call("session_renew", readJSON(r))
	if err != nil {
		writeJSON(w, 500, map[string]any{"ok": false, "error": err.Error()})
		return
	}
	writeJSON(w, 200, out)
}

func (s *Server) handleSessionKeepalive(w http.ResponseWriter, r *http.Request) {
	out, err := s.Bridge.Call("session_keepalive", map[string]any{"tick": true})
	if err != nil {
		writeJSON(w, 500, map[string]any{"ok": false, "error": err.Error()})
		return
	}
	writeJSON(w, 200, out)
}

func (s *Server) handleSessionDoctor(w http.ResponseWriter, r *http.Request) {
	fix := true
	if r.Method == http.MethodPost {
		body := readJSON(r)
		if v, ok := body["fix"].(bool); ok {
			fix = v
		}
	}
	out, err := s.Bridge.Call("session_doctor", map[string]any{"fix": fix})
	if err != nil {
		writeJSON(w, 500, map[string]any{"ok": false, "error": err.Error()})
		return
	}
	writeJSON(w, 200, map[string]any{"ok": out["ready"], "health": out["health"]})
}

func (s *Server) handleConvAck(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		writeJSON(w, 405, map[string]any{"ok": false})
		return
	}
	body := readJSON(r)
	uid, _ := body["user_id"].(string)
	if uid != "" {
		s.mu.Lock()
		delete(s.unread, uid)
		s.mu.Unlock()
	}
	writeJSON(w, 200, map[string]any{"ok": true, "user_id": uid})
}

func (s *Server) handleQRStart(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		writeJSON(w, 405, map[string]any{"ok": false})
		return
	}
	body := readJSON(r)
	out, err := s.Bridge.Call("qr_login_start", body)
	if err != nil {
		writeJSON(w, 500, map[string]any{"ok": false, "error": err.Error()})
		return
	}
	writeJSON(w, 200, out)
}

func (s *Server) handleQRStatus(w http.ResponseWriter, r *http.Request) {
	start := time.Now()
	out, err := s.Bridge.Call("qr_login_status", nil)
	if os.Getenv("PIGEON_DEV") == "1" {
		fmt.Fprintf(os.Stderr, "[api] qr-status %.0fms err=%v\n", float64(time.Since(start).Milliseconds()), err)
	}
	if err != nil {
		writeJSON(w, 500, map[string]any{"ok": false, "error": err.Error()})
		return
	}
	writeJSON(w, 200, out)
}

func (s *Server) handleQRImage(w http.ResponseWriter, r *http.Request) {
	candidates := []string{protocol.QRImagePath(s.Root)}
	if aid := protocol.ActiveAccountID(s.Root); aid != "" {
		candidates = append(candidates, filepath.Join(s.Root, "accounts", aid, "logs", "fxg_login_qr.png"))
	}
	candidates = append(candidates,
		filepath.Join(s.Root, "logs", "fxg_login_qr.png"),
		filepath.Join(s.Root, "session", "logs", "fxg_login_qr.png"),
	)
	var data []byte
	var err error
	for _, path := range candidates {
		data, err = os.ReadFile(path)
		if err == nil && len(data) > 0 {
			break
		}
	}
	if err != nil || len(data) == 0 {
		writeJSON(w, 404, map[string]any{"ok": false, "error": "qrcode not ready"})
		return
	}
	w.Header().Set("Content-Type", "image/png")
	w.Header().Set("Cache-Control", "no-cache")
	w.WriteHeader(200)
	_, _ = w.Write(data)
}

func (s *Server) handleCdpOnboardStart(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		writeJSON(w, 405, map[string]any{"ok": false})
		return
	}
	body := readJSON(r)
	out, err := s.Bridge.Call("cdp_onboard_start", body)
	if err != nil {
		writeJSON(w, 500, map[string]any{"ok": false, "error": err.Error()})
		return
	}
	writeJSON(w, 200, out)
}

func (s *Server) handleCdpOnboardStatus(w http.ResponseWriter, r *http.Request) {
	out, err := s.Bridge.Call("cdp_onboard_status", nil)
	if err != nil {
		writeJSON(w, 500, map[string]any{"ok": false, "error": err.Error()})
		return
	}
	writeJSON(w, 200, out)
}

func (s *Server) handleCdpWarmStart(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		writeJSON(w, 405, map[string]any{"ok": false})
		return
	}
	out, err := s.Bridge.Call("cdp_warm_start", readJSON(r))
	if err != nil {
		writeJSON(w, 500, map[string]any{"ok": false, "error": err.Error()})
		return
	}
	writeJSON(w, 200, out)
}

func (s *Server) handleCdpWarmStatus(w http.ResponseWriter, r *http.Request) {
	out, err := s.Bridge.Call("cdp_warm_status", nil)
	if err != nil {
		writeJSON(w, 500, map[string]any{"ok": false, "error": err.Error()})
		return
	}
	writeJSON(w, 200, out)
}

func (s *Server) handleAISuggest(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		writeJSON(w, 405, map[string]any{"ok": false})
		return
	}
	body := readJSON(r)
	out, err := s.Bridge.Call("ai_suggest", body)
	if err != nil {
		writeJSON(w, 500, map[string]any{"ok": false, "error": err.Error()})
		return
	}
	writeJSON(w, 200, out)
}

func (s *Server) handleProtocolStatus(w http.ResponseWriter, r *http.Request) {
	out, err := s.Bridge.Call("protocol_status", nil)
	if err != nil {
		writeJSON(w, 500, map[string]any{"ok": false, "error": err.Error()})
		return
	}
	writeJSON(w, 200, out)
}

func (s *Server) handleProtocolPrepare(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		writeJSON(w, 405, map[string]any{"ok": false})
		return
	}
	out, err := s.Bridge.Call("prepare_pure", map[string]any{"probe_ws": false})
	if err != nil {
		writeJSON(w, 500, map[string]any{"ok": false, "error": err.Error()})
		return
	}
	writeJSON(w, 200, out)
}

func (s *Server) handleImportHar(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		writeJSON(w, 405, map[string]any{"ok": false})
		return
	}
	body := readJSON(r)
	path, _ := body["path"].(string)
	if path == "" {
		path, _ = body["file"].(string)
	}
	if path == "" {
		writeJSON(w, 400, map[string]any{"ok": false, "error": "path required"})
		return
	}
	out, err := s.Bridge.Call("import_har", body)
	if err != nil {
		writeJSON(w, 500, map[string]any{"ok": false, "error": err.Error()})
		return
	}
	_, _ = s.Bridge.Call("prepare_pure", map[string]any{"probe_ws": false})
	writeJSON(w, 200, out)
}

func (s *Server) handleImportCookies(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		writeJSON(w, 405, map[string]any{"ok": false})
		return
	}
	body := readJSON(r)
	path, _ := body["path"].(string)
	if path == "" {
		path, _ = body["file"].(string)
	}
	if path == "" {
		writeJSON(w, 400, map[string]any{"ok": false, "error": "path required"})
		return
	}
	out, err := s.Bridge.Call("import_cookies", body)
	if err != nil {
		writeJSON(w, 500, map[string]any{"ok": false, "error": err.Error()})
		return
	}
	writeJSON(w, 200, out)
}

func (s *Server) handleSessionPackExport(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		writeJSON(w, 405, map[string]any{"ok": false})
		return
	}
	body := readJSON(r)
	path, _ := body["path"].(string)
	if path == "" {
		path, _ = body["file"].(string)
	}
	if path == "" {
		path = protocol.SessionPackDefaultPath(s.Root)
	}
	out, err := s.Bridge.Call("export_session_pack", map[string]any{"path": path})
	if err != nil {
		writeJSON(w, 500, map[string]any{"ok": false, "error": err.Error()})
		return
	}
	writeJSON(w, 200, out)
}

func (s *Server) handleSessionPackImport(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		writeJSON(w, 405, map[string]any{"ok": false})
		return
	}
	body := readJSON(r)
	path, _ := body["path"].(string)
	if path == "" {
		path, _ = body["file"].(string)
	}
	if path == "" {
		writeJSON(w, 400, map[string]any{"ok": false, "error": "path required"})
		return
	}
	out, err := s.Bridge.Call("import_session_pack", body)
	if err != nil {
		writeJSON(w, 500, map[string]any{"ok": false, "error": err.Error()})
		return
	}
	writeJSON(w, 200, out)
}
