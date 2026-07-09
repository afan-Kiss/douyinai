package bridge

import (
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"time"
)

type StatusSnapshot struct {
	DaemonOK      bool
	PythonPID     int
	LastStartAt   time.Time
	LastError     string
	RestartCount  int
	LastPingMs    int64
	DegradedSince time.Time
}

func (c *Client) GetStatus() StatusSnapshot {
	c.statusMu.RLock()
	defer c.statusMu.RUnlock()
	c.mu.Lock()
	pid := 0
	if c.daemon != nil && c.daemon.Process != nil {
		pid = c.daemon.Process.Pid
	}
	live := c.daemonOK && pid > 0
	c.mu.Unlock()
	return StatusSnapshot{
		DaemonOK:      live,
		PythonPID:     pid,
		LastStartAt:   c.lastStartAt,
		LastError:     c.lastError,
		RestartCount:  c.restartCount,
		LastPingMs:    c.lastPingMs,
		DegradedSince: c.degradedSince,
	}
}

func (c *Client) appendStartupLog(reason string, extra string) {
	dir := filepath.Join(c.Root, "logs", "runtime")
	_ = os.MkdirAll(dir, 0o755)
	path := filepath.Join(dir, "bridge_startup.log")
	f, err := os.OpenFile(path, os.O_CREATE|os.O_APPEND|os.O_WRONLY, 0o644)
	if err != nil {
		return
	}
	defer f.Close()
	_, _ = fmt.Fprintf(f, "=== %s ===\n", time.Now().Format(time.RFC3339))
	_, _ = fmt.Fprintf(f, "reason: %s\n", reason)
	_, _ = fmt.Fprintf(f, "python: %s\n", c.Python)
	_, _ = fmt.Fprintf(f, "cwd: %s\n", c.Root)
	for _, k := range []string{"PIGEON_PROJECT_ROOT", "PIGEON_ROOT", "PIGEON_PYTHON", "PIGEON_NO_CDP", "PIGEON_PURE_ONLY", "PATH"} {
		if v := os.Getenv(k); v != "" {
			_, _ = fmt.Fprintf(f, "%s=%s\n", k, v)
		}
	}
	if extra != "" {
		_, _ = fmt.Fprintf(f, "detail:\n%s\n", extra)
	}
	_, _ = fmt.Fprintf(f, "\n")
}

func (c *Client) readRecentStderr(maxLines int) string {
	c.mu.Lock()
	errR := c.daemonErr
	c.mu.Unlock()
	if errR == nil {
		return ""
	}
	buf := make([]byte, 8192)
	n, _ := errR.Read(buf)
	text := string(buf[:n])
	lines := strings.Split(text, "\n")
	if len(lines) > maxLines {
		lines = lines[len(lines)-maxLines:]
	}
	return strings.Join(lines, "\n")
}

func (c *Client) noteStartFailure(reason string) {
	c.statusMu.Lock()
	defer c.statusMu.Unlock()
	c.lastError = reason
	if c.degradedSince.IsZero() {
		c.degradedSince = time.Now()
	}
	stderr := c.readRecentStderr(200)
	c.appendStartupLog(reason, stderr)
}

func (c *Client) noteStartSuccess() {
	c.statusMu.Lock()
	defer c.statusMu.Unlock()
	c.lastStartAt = time.Now()
	c.lastError = ""
	c.degradedSince = time.Time{}
}

func (c *Client) RecordPingMs(ms int64) {
	c.statusMu.Lock()
	c.lastPingMs = ms
	c.statusMu.Unlock()
}

func (c *Client) RestartDaemon() (bool, string) {
	c.startMu.Lock()
	defer c.startMu.Unlock()
	c.mu.Lock()
	c.resetDaemonLocked()
	c.daemonOK = false
	c.mu.Unlock()
	c.CleanupNodes("bridge_restart")
	c.statusMu.Lock()
	c.restartCount++
	c.statusMu.Unlock()
	ok := c.startDaemon()
	c.mu.Lock()
	c.daemonOK = ok
	c.mu.Unlock()
	if !ok {
		c.noteStartFailure("restart failed")
		return false, "restart failed"
	}
	c.noteStartSuccess()
	return true, ""
}

func (c *Client) StartDaemonBackground() {
	go func() {
		defer func() {
			if r := recover(); r != nil {
				c.noteStartFailure(fmt.Sprintf("background start panic: %v", r))
			}
		}()
		_ = c.EnsureDaemonWait(20 * time.Second)
	}()
}
