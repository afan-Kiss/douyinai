package api

import (
	"fmt"
	"os"
	"path/filepath"
	"runtime/debug"
	"sync"
	"time"
)

var panicLogMu sync.Mutex

func logPanic(source string, rec any) {
	panicLogMu.Lock()
	defer panicLogMu.Unlock()
	root := os.Getenv("PIGEON_PROJECT_ROOT")
	if root == "" {
		root = os.Getenv("PIGEON_ROOT")
	}
	if root == "" {
		root = "."
	}
	dir := filepath.Join(root, "logs", "runtime")
	_ = os.MkdirAll(dir, 0o755)
	path := filepath.Join(dir, "go_api_panic.log")
	f, err := os.OpenFile(path, os.O_CREATE|os.O_APPEND|os.O_WRONLY, 0o644)
	if err != nil {
		fmt.Fprintf(os.Stderr, "[pigeon-feige] panic log open failed: %v\n", err)
		return
	}
	defer f.Close()
	_, _ = fmt.Fprintf(f, "=== %s %s source=%s ===\n%s\n\n", time.Now().Format(time.RFC3339), "panic", source, debug.Stack())
	fmt.Fprintf(os.Stderr, "[pigeon-feige] panic in %s: %v\n", source, rec)
}
