package bridge

import (
	"bufio"
	"bytes"
	"encoding/json"
	"fmt"
	"io"
	"os"
	"os/exec"
	"path/filepath"
	"sync"
	"time"

	"pigeon-feige/internal/execwin"
)

// Client calls pigeon_protocol go_bridge — persistent daemon or one-shot fallback.
type Client struct {
	Root   string
	Python string
	mu     sync.Mutex
	rpcMu  sync.Mutex

	daemon    *exec.Cmd
	daemonIn  io.WriteCloser
	daemonOut *bufio.Reader
	daemonErr io.ReadCloser
	daemonOK  bool

	lastDaemonRestart time.Time
}

func NewClient(root string) *Client {
	py := os.Getenv("PIGEON_PYTHON")
	if py == "" {
		for _, rel := range []string{
			"runtime/python/python.exe",
			"runtime/python3/python.exe",
		} {
			p := filepath.Join(root, rel)
			if st, err := os.Stat(p); err == nil && !st.IsDir() {
				py = p
				break
			}
		}
	}
	if py == "" {
		for _, name := range []string{"python", "python3", "py"} {
			if p, err := exec.LookPath(name); err == nil {
				py = p
				break
			}
		}
	}
	if py == "" {
		py = "python"
	}
	c := &Client{Root: root, Python: py}
	return c
}

func (c *Client) bridgeEnv() []string {
	env := append(os.Environ(),
		"PYTHONIOENCODING=utf-8",
		"PYTHONUTF8=1",
		"PYTHONUNBUFFERED=1",
		"PIGEON_STANDALONE=1",
		"PIGEON_PROJECT_ROOT="+c.Root,
		"PIGEON_ROOT="+c.Root,
		"PIGEON_PURE_ONLY="+envOr("PIGEON_PURE_ONLY", "1"),
		"PIGEON_NO_CDP="+envOr("PIGEON_NO_CDP", "1"),
		"PIGEON_WS_HOST="+envOr("PIGEON_WS_HOST", "jinritemai"),
		"PIGEON_NODE_MAX_PROCS="+envOr("PIGEON_NODE_MAX_PROCS", "2"),
		"PIGEON_NODE_ONESHOT_FALLBACK="+envOr("PIGEON_NODE_ONESHOT_FALLBACK", "0"),
	)
	if c.Python != "" {
		env = append(env, "PIGEON_PYTHON="+c.Python)
	}
	for _, rel := range []string{"runtime/node/node.exe", "node/node.exe"} {
		node := filepath.Join(c.Root, rel)
		if st, err := os.Stat(node); err == nil && !st.IsDir() {
			env = append(env, "PIGEON_NODE="+node)
			break
		}
	}
	return env
}

func (c *Client) resetDaemon() {
	if c.daemonIn != nil {
		_ = c.daemonIn.Close()
		c.daemonIn = nil
	}
	if c.daemon != nil && c.daemon.Process != nil {
		_ = c.daemon.Process.Kill()
	}
	c.daemon = nil
	c.daemonOut = nil
	c.daemonErr = nil
	c.daemonOK = false
}

func (c *Client) CleanupNodes(reason string) {
	c.CleanupNodesWithOptions(reason, false, 6*3600)
}

func (c *Client) CleanupNodesAll(reason string) {
	c.CleanupNodesWithOptions(reason, true, 0)
}

func (c *Client) CleanupNodesWithOptions(reason string, killAll bool, olderThanSec int) {
	params := map[string]any{"reason": reason, "kill_all": killAll}
	if !killAll && olderThanSec > 0 {
		params["kill_all"] = false
		params["older_than_sec"] = olderThanSec
	}
	body, err := json.Marshal(map[string]any{
		"action": "process_guard_cleanup",
		"params": params,
	})
	if err != nil {
		return
	}
	if c.daemonOK && c.daemonIn != nil {
		c.rpcMu.Lock()
		_, _ = c.callDaemonWithTimeout(string(body), 2*time.Second)
		c.rpcMu.Unlock()
	}
	_, _ = c.callOneShot(body)
}

func (c *Client) requireDaemon(action string) bool {
	switch action {
	case "qr_login_start", "qr_login_status", "session_status", "listen_start", "listen_stop", "listen_status", "events":
		return true
	default:
		return false
	}
}

func (c *Client) startDaemon() bool {
	if os.Getenv("PIGEON_BRIDGE_ONESHOT") == "1" {
		return false
	}
	runPy := filepath.Join(c.Root, "run.py")
	cmd := exec.Command(c.Python, runPy, "go-bridge", "--daemon")
	execwin.Configure(cmd)
	cmd.Dir = c.Root
	cmd.Env = c.bridgeEnv()

	stdin, err := cmd.StdinPipe()
	if err != nil {
		return false
	}
	stdout, err := cmd.StdoutPipe()
	if err != nil {
		return false
	}
	stderr, err := cmd.StderrPipe()
	if err != nil {
		return false
	}
	if err := cmd.Start(); err != nil {
		return false
	}
	c.daemon = cmd
	c.daemonIn = stdin
	c.daemonOut = bufio.NewReader(stdout)
	c.daemonErr = stderr

	go func() {
		sc := bufio.NewScanner(stderr)
		for sc.Scan() {
			if os.Getenv("PIGEON_DEV") == "1" {
				fmt.Fprintf(os.Stderr, "[bridge] %s\n", sc.Text())
			}
		}
	}()

	deadline := time.Now().Add(15 * time.Second)
	for time.Now().Before(deadline) {
		out, err := c.callDaemonLocked(`{"action":"ping","params":{}}`)
		if err == nil {
			if ok, _ := out["ok"].(bool); ok {
				return true
			}
		}
		time.Sleep(200 * time.Millisecond)
	}
	c.resetDaemon()
	return false
}

func (c *Client) callDaemonLocked(line string) (map[string]any, error) {
	if c.daemonIn == nil || c.daemonOut == nil {
		return nil, fmt.Errorf("daemon not running")
	}
	if _, err := io.WriteString(c.daemonIn, line+"\n"); err != nil {
		return nil, err
	}
	respLine, err := c.daemonOut.ReadString('\n')
	if err != nil {
		return nil, err
	}
	var out map[string]any
	if err := json.Unmarshal([]byte(respLine), &out); err != nil {
		return nil, fmt.Errorf("daemon decode: %w", err)
	}
	return out, nil
}

func (c *Client) callDaemonWithTimeout(line string, timeout time.Duration) (map[string]any, error) {
	type result struct {
		out map[string]any
		err error
	}
	ch := make(chan result, 1)
	go func() {
		out, err := c.callDaemonLocked(line)
		ch <- result{out, err}
	}()
	select {
	case res := <-ch:
		return res.out, res.err
	case <-time.After(timeout):
		// Kill daemon so blocked ReadString in the goroutine unblocks; then reap it.
		c.resetDaemon()
		select {
		case <-ch:
		case <-time.After(3 * time.Second):
		}
		return nil, fmt.Errorf("daemon call timeout")
	}
}

func (c *Client) callOneShot(body []byte) (map[string]any, error) {
	runPy := filepath.Join(c.Root, "run.py")
	cmd := exec.Command(c.Python, runPy, "go-bridge")
	execwin.Configure(cmd)
	cmd.Dir = c.Root
	cmd.Env = c.bridgeEnv()
	cmd.Stdin = bytes.NewReader(body)
	var stdout, stderr bytes.Buffer
	cmd.Stdout = &stdout
	cmd.Stderr = &stderr
	if err := cmd.Run(); err != nil {
		msg := stderr.String()
		if msg == "" {
			msg = err.Error()
		}
		return nil, fmt.Errorf("bridge: %s", msg)
	}
	var out map[string]any
	if err := json.Unmarshal(stdout.Bytes(), &out); err != nil {
		return nil, fmt.Errorf("bridge decode: %w", err)
	}
	return out, nil
}

func (c *Client) callTimeout(action string) time.Duration {
	switch action {
	case "ping", "session_status", "qr_login_status", "list_accounts", "listen_status", "health", "process_status":
		return 3 * time.Second
	case "qr_login_start":
		return 8 * time.Second
	case "prepare_pure", "warm_conv", "session_doctor":
		return 90 * time.Second
	default:
		return 25 * time.Second
	}
}

func (c *Client) ensureDaemon() bool {
	c.mu.Lock()
	defer c.mu.Unlock()
	if c.daemonOK && c.daemon != nil {
		return true
	}
	c.daemonOK = c.startDaemon()
	return c.daemonOK
}

func (c *Client) canRestartDaemon() bool {
	if c.lastDaemonRestart.IsZero() {
		return true
	}
	return time.Since(c.lastDaemonRestart) >= 30*time.Second
}

func (c *Client) Call(action string, params map[string]any) (map[string]any, error) {
	req := map[string]any{"action": action, "params": params}
	body, err := json.Marshal(req)
	if err != nil {
		return nil, err
	}
	timeout := c.callTimeout(action)

	useDaemon := c.ensureDaemon()
	if c.requireDaemon(action) && !useDaemon {
		return nil, fmt.Errorf("bridge daemon not ready")
	}

	var out map[string]any
	if useDaemon {
		c.rpcMu.Lock()
		out, err = c.callDaemonWithTimeout(string(body), timeout)
		c.rpcMu.Unlock()
		if err != nil {
			c.mu.Lock()
			if c.requireDaemon(action) && c.canRestartDaemon() {
				c.CleanupNodes("daemon_timeout")
				c.resetDaemon()
				c.lastDaemonRestart = time.Now()
				c.daemonOK = c.startDaemon()
				if c.daemonOK {
					c.rpcMu.Lock()
					out, err = c.callDaemonWithTimeout(string(body), timeout)
					c.rpcMu.Unlock()
				}
			} else if c.requireDaemon(action) {
				err = fmt.Errorf("bridge daemon restarting, please retry")
			}
			c.mu.Unlock()
			if err != nil && !c.requireDaemon(action) {
				out, err = c.callOneShot(body)
			}
		}
	} else {
		out, err = c.callOneShot(body)
	}
	if err != nil {
		return nil, err
	}
	return c.normalizeOut(out, action)
}

func (c *Client) normalizeOut(out map[string]any, action string) (map[string]any, error) {
	if ok, _ := out["ok"].(bool); !ok {
		switch action {
		case "conv_list":
			if n, ok := out["count"].(float64); ok && n > 0 {
				return out, nil
			}
			if items, ok := out["items"].([]any); ok && len(items) > 0 {
				return out, nil
			}
		case "prepare_pure":
			if report, ok := out["report"].(map[string]any); ok {
				if steps, ok := report["steps"].([]any); ok && len(steps) > 0 {
					return out, nil
				}
			}
		case "qr_login_start":
			return out, nil
		}
		if e, _ := out["error"].(string); e != "" {
			return out, fmt.Errorf("%s", e)
		}
	}
	return out, nil
}

func (c *Client) PreparePure() error {
	out, err := c.Call("prepare_pure", map[string]any{"probe_ws": false})
	if err != nil {
		return err
	}
	if ready, _ := out["ready"].(bool); !ready {
		if report, ok := out["report"].(map[string]any); ok {
			if steps, ok := report["steps"].([]any); ok && len(steps) > 0 {
				return nil
			}
		}
		return fmt.Errorf("prepare_pure not ready")
	}
	return nil
}

func (c *Client) Ping() error {
	_, err := c.Call("ping", nil)
	return err
}

func (c *Client) Close() {
	c.mu.Lock()
	defer c.mu.Unlock()
	c.CleanupNodesAll("bridge_close")
	c.resetDaemon()
}

func envOr(k, def string) string {
	if v := os.Getenv(k); v != "" {
		return v
	}
	return def
}

func (c *Client) PrepareAsync(timeout time.Duration) {
	if os.Getenv("PIGEON_ENABLE_BACKGROUND_PREPARE") != "1" {
		return
	}
	root := c.Root
	py := c.Python
	go func() {
		bg := &Client{Root: root, Python: py, daemonOK: false}
		deadline := time.Now().Add(timeout)
		for time.Now().Before(deadline) {
			if err := bg.Ping(); err == nil {
				_, _ = bg.Call("warm_conv", nil)
				_ = bg.PreparePure()
				return
			}
			time.Sleep(300 * time.Millisecond)
		}
	}()
}
