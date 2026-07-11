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
	unread  map[string]map[string]int
}

func NewServer(root string) *Server {
	return &Server{
		Root:   root,
		Bridge: bridge.NewClient(root),
		UIDir:  filepath.Join(root, "desktop", "ui"),
		unread: map[string]map[string]int{},
	}
}

func (s *Server) Handler() http.Handler {
	mux := http.NewServeMux()
	mux.HandleFunc("/", s.handleIndex)
	mux.HandleFunc("/static/", s.handleStatic)
	mux.HandleFunc("/api/health", s.handleHealth)
	mux.HandleFunc("/api/bridge/status", s.handleBridgeStatus)
	mux.HandleFunc("/api/bridge/restart", s.handleBridgeRestart)
	mux.HandleFunc("/api/session", s.handleSession)
	mux.HandleFunc("/api/accounts", s.handleAccounts)
	mux.HandleFunc("/api/accounts/switch", s.handleAccountsSwitch)
	mux.HandleFunc("/api/accounts/create", s.handleAccountsCreate)
	mux.HandleFunc("/api/accounts/logout", s.handleAccountsLogout)
	mux.HandleFunc("/api/accounts/remove", s.handleAccountsRemove)
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
	mux.HandleFunc("/api/process/status", s.handleProcessStatus)
	mux.HandleFunc("/api/process/cleanup", s.handleProcessCleanup)
	mux.HandleFunc("/api/import-har", s.handleImportHar)
	mux.HandleFunc("/api/import-cookies", s.handleImportCookies)
	mux.HandleFunc("/api/session-pack/export", s.handleSessionPackExport)
	mux.HandleFunc("/api/session-pack/import", s.handleSessionPackImport)
	return recoverHTTP(withCORS(mux))
}

func (s *Server) StartBackgroundPrepare() {
	s.Bridge.StartDaemonBackground()
	if os.Getenv("PIGEON_ENABLE_STARTUP_WARM") != "1" {
		return
	}
	go func() {
		defer func() {
			if r := recover(); r != nil {
				logPanic("warmCSRF", r)
			}
		}()
		warmCSRF(s.Root)
	}()
}

func (s *Server) activeAccountID() string {
	return protocol.ActiveAccountID(s.Root)
}

func (s *Server) bumpUnread(accountID, uid string) {
	if accountID == "" || uid == "" {
		return
	}
	s.mu.Lock()
	defer s.mu.Unlock()
	if s.unread[accountID] == nil {
		s.unread[accountID] = map[string]int{}
	}
	s.unread[accountID][uid] = s.unread[accountID][uid] + 1
}

func (s *Server) clearUnreadAccount(accountID string) {
	if accountID == "" {
		return
	}
	s.mu.Lock()
	delete(s.unread, accountID)
	s.mu.Unlock()
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
	goApiOk := true
	st := s.Bridge.GetStatus()
	pythonLive := st.DaemonOK
	if !pythonLive {
		pythonLive = s.Bridge.EnsureDaemonWait(2 * time.Second)
		st = s.Bridge.GetStatus()
	}
	bridgeReady := false
	pingMs := -1
	if pythonLive {
		t0 := time.Now()
		_, err := bridgeCallWithTimeout(s.Bridge, "ping", map[string]any{"oneshot": true}, 3*time.Second)
		pingMs = int(time.Since(t0).Milliseconds())
		s.Bridge.RecordPingMs(int64(pingMs))
		bridgeReady = err == nil
	}
	degraded := !bridgeReady
	writeJSON(w, 200, map[string]any{
		"ok":                 goApiOk,
		"go_api_ok":          goApiOk,
		"bridge_ready":       bridgeReady,
		"python_daemon_live": pythonLive && st.PythonPID > 0,
		"bridge_ping_ms":     pingMs,
		"degraded":           degraded,
		"via":                "go/bridge",
		"health": map[string]any{
			"bridge_ready":       bridgeReady,
			"python_daemon_live": pythonLive && st.PythonPID > 0,
			"restart_count":      st.RestartCount,
			"last_error":         st.LastError,
		},
	})
}

func (s *Server) handleBridgeStatus(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodGet {
		writeJSON(w, 405, map[string]any{"ok": false})
		return
	}
	st := s.Bridge.GetStatus()
	lastStart := ""
	if !st.LastStartAt.IsZero() {
		lastStart = st.LastStartAt.Format(time.RFC3339)
	}
	degradedSince := ""
	if !st.DegradedSince.IsZero() {
		degradedSince = st.DegradedSince.Format(time.RFC3339)
	}
	writeJSON(w, 200, map[string]any{
		"ok":            true,
		"daemonOK":      st.DaemonOK,
		"python_pid":    st.PythonPID,
		"last_start_at": lastStart,
		"last_error":    st.LastError,
		"restart_count": st.RestartCount,
		"last_ping_ms":  st.LastPingMs,
		"degraded_since": degradedSince,
	})
}

func (s *Server) handleBridgeRestart(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		writeJSON(w, 405, map[string]any{"ok": false})
		return
	}
	ok, msg := s.Bridge.RestartDaemon()
	if !ok {
		writeJSON(w, 200, map[string]any{"ok": false, "error": msg, "restarted": false})
		return
	}
	deadline := time.Now().Add(5 * time.Second)
	pingOk := false
	for time.Now().Before(deadline) {
		if err := s.Bridge.Ping(); err == nil {
			pingOk = true
			break
		}
		time.Sleep(200 * time.Millisecond)
	}
	st := s.Bridge.GetStatus()
	writeJSON(w, 200, map[string]any{
		"ok":         pingOk,
		"restarted":  true,
		"ping_ok":    pingOk,
		"python_pid": st.PythonPID,
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
	if r.URL.Query().Get("light") == "1" {
		resp["active_account_id"] = protocol.ActiveAccountID(s.Root)
		if acct, err := bridgeCallWithTimeout(s.Bridge, "list_accounts", map[string]any{"fast": true}, 2*time.Second); err == nil && acct != nil {
			if v, ok := acct["accounts"]; ok {
				resp["accounts"] = v
			}
			if v, ok := acct["active_account_id"].(string); ok && v != "" {
				resp["active_account_id"] = v
			}
		}
		if sess != nil && sess.LoggedIn() {
			resp["send_ready"] = sess.QueryTokens["pigeon_sign"] != "" && len(sess.WSUrls) > 0
			resp["listen_ready"] = true
			resp["session_alive"] = true
		}
		if os.Getenv("PIGEON_DEV") == "1" {
			fmt.Fprintf(os.Stderr, "[api] session light %.0fms\n", float64(time.Since(start).Milliseconds()))
		}
		writeJSON(w, 200, resp)
		return
	}
	bridgeParams := map[string]any{"light": false}
	out, err := bridgeCallWithTimeout(s.Bridge, "session_status", bridgeParams, 8*time.Second)
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
	prevAID := protocol.ActiveAccountID(s.Root)
	out, err := s.Bridge.Call("switch_account", map[string]any{
		"account_id":      accountID,
		"restart_listen":  restartListen,
	})
	if err != nil {
		writeJSON(w, 500, map[string]any{"ok": false, "error": err.Error()})
		return
	}
	s.clearUnreadAccount(prevAID)
	writeJSON(w, 200, out)
}

func (s *Server) handleAccountsLogout(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		writeJSON(w, 405, map[string]any{"ok": false})
		return
	}
	body := readJSON(r)
	prevAID := s.activeAccountID()
	params := map[string]any{"backup": true}
	if v, ok := body["account_id"].(string); ok && v != "" {
		params["account_id"] = v
	}
	if v, ok := body["backup"].(bool); ok {
		params["backup"] = v
	}
	out, err := s.Bridge.Call("account_logout", params)
	if err != nil {
		writeJSON(w, 500, map[string]any{"ok": false, "error": err.Error()})
		return
	}
	s.clearUnreadAccount(prevAID)
	writeJSON(w, 200, out)
}

func (s *Server) handleAccountsRemove(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		writeJSON(w, 405, map[string]any{"ok": false})
		return
	}
	body := readJSON(r)
	prevAID := s.activeAccountID()
	params := map[string]any{"backup": true, "confirm": false}
	if v, ok := body["account_id"].(string); ok && v != "" {
		params["account_id"] = v
	}
	if v, ok := body["backup"].(bool); ok {
		params["backup"] = v
	}
	if v, ok := body["confirm"].(bool); ok {
		params["confirm"] = v
	}
	out, err := s.Bridge.Call("account_remove", params)
	if err != nil {
		writeJSON(w, 500, map[string]any{"ok": false, "error": err.Error()})
		return
	}
	s.clearUnreadAccount(prevAID)
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
	light := r.URL.Query().Get("light") == "1"
	params := map[string]any{"page": page, "size": size, "light": light}
	if category := strings.TrimSpace(r.URL.Query().Get("category")); category != "" {
		params["category"] = category
	}
	if !light {
		warmCSRF(s.Root)
	}
	var out map[string]any
	var err error
	if light {
		out, err = bridgeCallWithTimeout(s.Bridge, "conv_list", params, 4*time.Second)
	} else {
		out, err = s.Bridge.Call("conv_list", params)
	}
	if !light && (err != nil || !convListOK(out)) {
		_, _ = s.Bridge.Call("session_doctor", map[string]any{"fix": true})
		out, err = s.Bridge.Call("conv_list", params)
	}
	if !light && (err != nil || !convListOK(out)) {
		_, _ = s.Bridge.Call("refresh_conv_snapshot", map[string]any{"cdp_only": true, "size": size})
		out, err = s.Bridge.Call("conv_list", params)
	}
	if err != nil {
		if light {
			writeJSON(w, 200, map[string]any{
				"ok":           false,
				"items":        []any{},
				"count":        0,
				"light":        true,
				"needs_repair": true,
				"error":        "session refresh unavailable",
			})
			return
		}
		writeJSON(w, 500, map[string]any{"ok": false, "error": err.Error()})
		return
	}
	s.applyUnreadBumps(out)
	resp := map[string]any{
		"ok":    convListOK(out),
		"items": out["items"],
		"raw":   out["raw"],
		"via":   "go/bridge",
	}
	if light {
		resp["light"] = true
		if v, ok := out["source"]; ok {
			resp["source"] = v
		}
		if v, ok := out["warning"]; ok {
			resp["warning"] = v
		}
		if v, ok := out["needs_repair"]; ok {
			resp["needs_repair"] = v
		}
		if v, ok := out["error"]; ok {
			resp["error"] = v
		}
		if v, ok := out["count"]; ok {
			resp["count"] = v
		}
		if convListOK(out) {
			resp["ok"] = true
		}
	}
	writeJSON(w, 200, resp)
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
	aid := s.activeAccountID()
	s.mu.Lock()
	bumps := map[string]int{}
	if aid != "" && s.unread[aid] != nil {
		for k, v := range s.unread[aid] {
			bumps[k] = v
		}
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

func degradedContextPayload(msg string) map[string]any {
	return map[string]any{
		"ok": false,
		"context": map[string]any{
			"messages":   []any{},
			"buyer_name": "",
			"source":     "degraded",
		},
		"message_count": 0,
		"error":         msg,
	}
}

func degradedOrdersPayload(msg string) map[string]any {
	return map[string]any{
		"ok":        false,
		"order_ok":  false,
		"has_order": false,
		"orders": map[string]any{
			"has_order": false,
			"cards":     []any{},
			"summary":   "订单暂时不可用",
		},
		"source": "degraded",
		"error":  msg,
	}
}

func degradedOrdersFastPayload(msg string) map[string]any {
	if msg == "" {
		msg = "订单加载较慢，可点击重试"
	}
	return map[string]any{
		"ok":        false,
		"order_ok":  false,
		"has_order": false,
		"orders": map[string]any{
			"has_order": false,
			"cards":     []any{},
			"summary":   msg,
		},
		"source": "degraded_fast",
		"error":  msg,
	}
}

func bridgeCallWithTimeout(b *bridge.Client, action string, params map[string]any, timeout time.Duration) (map[string]any, error) {
	type result struct {
		out map[string]any
		err error
	}
	ch := make(chan result, 1)
	go func() {
		defer func() {
			if r := recover(); r != nil {
				logPanic("bridgeCall:"+action, r)
				ch <- result{err: fmt.Errorf("bridge panic: %v", r)}
			}
		}()
		out, err := b.Call(action, params)
		ch <- result{out: out, err: err}
	}()
	select {
	case res := <-ch:
		return res.out, res.err
	case <-time.After(timeout):
		return nil, fmt.Errorf("%s timeout", action)
	}
}

func (s *Server) handleContext(w http.ResponseWriter, r *http.Request) {
	uid := r.URL.Query().Get("user_id")
	if uid == "" {
		writeJSON(w, 400, map[string]any{"ok": false, "error": "user_id required"})
		return
	}
	if !s.Bridge.EnsureDaemonWait(2 * time.Second) {
		writeJSON(w, 200, degradedContextPayload("聊天记录暂时不可用"))
		return
	}
	out, err := bridgeCallWithTimeout(s.Bridge, "context", map[string]any{"user_id": uid}, 6*time.Second)
	s.mu.Lock()
	aid := s.activeAccountID()
	if aid != "" && s.unread[aid] != nil {
		delete(s.unread[aid], uid)
	}
	s.mu.Unlock()
	if err != nil {
		writeJSON(w, 200, degradedContextPayload("聊天记录暂时不可用"))
		return
	}
	ctx, _ := out["context"].(map[string]any)
	if ctx == nil {
		writeJSON(w, 200, degradedContextPayload("聊天记录暂时不可用"))
		return
	}
	if _, ok := ctx["messages"]; !ok {
		ctx["messages"] = []any{}
	}
	msgCount := 0
	if mc, ok := out["message_count"].(float64); ok {
		msgCount = int(mc)
	} else if msgs, ok := ctx["messages"].([]any); ok {
		msgCount = len(msgs)
	}
	resp := map[string]any{"ok": msgCount > 0, "context": ctx, "message_count": msgCount}
	if e, _ := out["error"].(string); e != "" {
		resp["error"] = e
	}
	writeJSON(w, 200, resp)
}

func (s *Server) handleOrders(w http.ResponseWriter, r *http.Request) {
	uid := r.URL.Query().Get("user_id")
	if uid == "" {
		writeJSON(w, 400, map[string]any{"ok": false, "error": "user_id required"})
		return
	}
	fast := r.URL.Query().Get("fast") == "1"
	heavy := r.URL.Query().Get("heavy") == "1"
	if !heavy && !fast {
		fast = true
	}
	timeout := 3 * time.Second
	if heavy {
		timeout = 10 * time.Second
	}
	params := map[string]any{"user_id": uid, "fast": fast, "heavy": heavy}
	if heavy {
		params["oneshot"] = true
	} else if !s.Bridge.EnsureDaemonWait(2 * time.Second) {
		writeJSON(w, 200, degradedOrdersFastPayload("订单加载较慢，可点击重试"))
		return
	}
	out, err := bridgeCallWithTimeout(s.Bridge, "orders", params, timeout)
	if err != nil {
		if fast {
			writeJSON(w, 200, degradedOrdersFastPayload("订单加载较慢，可点击重试"))
		} else {
			writeJSON(w, 200, degradedOrdersPayload("订单加载失败，可稍后重试"))
		}
		return
	}
	orders, _ := out["orders"].(map[string]any)
	if orders == nil {
		if fast {
			writeJSON(w, 200, degradedOrdersFastPayload("订单加载较慢，可点击重试"))
		} else {
			writeJSON(w, 200, degradedOrdersPayload("订单加载失败，可稍后重试"))
		}
		return
	}
	if _, ok := orders["cards"]; !ok {
		orders["cards"] = []any{}
	}
	ok := false
	if v, _ := out["order_ok"].(bool); v {
		ok = true
	}
	if v, _ := out["has_order"].(bool); v {
		ok = true
	}
	if v, _ := orders["has_order"].(bool); v {
		ok = true
	}
	if cards, _ := orders["cards"].([]any); len(cards) > 0 {
		ok = true
	}
	resp := map[string]any{"ok": ok, "orders": orders, "source": out["source"], "order_ok": ok}
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
	filterAID := strings.TrimSpace(r.URL.Query().Get("account_id"))
	params := map[string]any{"since": since}
	if filterAID != "" {
		params["account_id"] = filterAID
	}
	out, err := s.Bridge.Call("events", params)
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
			evtAID, _ := m["account_id"].(string)
			if uid != "" && evtAID != "" {
				s.bumpUnread(evtAID, uid)
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
	params := readJSON(r)
	params["oneshot"] = true
	out, err := bridgeCallWithTimeout(s.Bridge, "session_bootstrap", params, 3*time.Second)
	if err != nil {
		writeJSON(w, 200, map[string]any{"ok": true, "started": false, "degraded": true, "error": err.Error()})
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
	out, err := bridgeCallWithTimeout(s.Bridge, "session_keepalive", map[string]any{"tick": true, "oneshot": true}, 5*time.Second)
	if err != nil {
		writeJSON(w, 200, map[string]any{"ok": true, "degraded": true, "error": err.Error()})
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
		aid := s.activeAccountID()
		if aid != "" && s.unread[aid] != nil {
			delete(s.unread[aid], uid)
		}
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
	out, err := bridgeCallWithTimeout(s.Bridge, "protocol_status", map[string]any{"oneshot": true}, 15*time.Second)
	if err != nil {
		writeJSON(w, 200, map[string]any{
			"ok":             false,
			"foundation_ok":  false,
			"send_ready":     false,
			"listen_ready":   false,
			"conv_snapshot":  false,
			"degraded":       true,
			"error":          err.Error(),
		})
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

func (s *Server) handleProcessStatus(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodGet {
		writeJSON(w, 405, map[string]any{"ok": false})
		return
	}
	out, err := s.Bridge.Call("process_status", nil)
	if err != nil {
		writeJSON(w, 500, map[string]any{"ok": false, "error": err.Error()})
		return
	}
	writeJSON(w, 200, out)
}

func (s *Server) handleProcessCleanup(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		writeJSON(w, 405, map[string]any{"ok": false})
		return
	}
	body := readJSON(r)
	killAll := true
	if v, ok := body["kill_all"].(bool); ok {
		killAll = v
	}
	params := map[string]any{"kill_all": killAll}
	if v, ok := body["older_than_sec"].(float64); ok && v > 0 {
		params["older_than_sec"] = int(v)
	}
	out, err := s.Bridge.Call("process_cleanup", params)
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
