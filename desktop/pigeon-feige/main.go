package main

import (
	"context"
	"fmt"
	"net"
	"net/http"
	"os"
	"os/exec"
	"os/signal"
	"path/filepath"
	"sync"
	"syscall"
	"strings"
	"time"
	"unsafe"

	"github.com/inkeliz/gowebview"

	"pigeon-feige/internal/api"
	"pigeon-feige/internal/bridge"
	"pigeon-feige/internal/execwin"
)

const apiURL = "http://127.0.0.1:8765/"

var (
	goAPIServer *api.Server
	apiListener net.Listener
	httpServer  *http.Server
)

func main() {
	root := findProjectRoot()
	useGoAPI := os.Getenv("PIGEON_PYTHON_API") != "1"

	var pyCmd *exec.Cmd
	if useGoAPI {
		if err := startGoAPI(root); err != nil {
			fail(fmt.Sprintf("Go API 启动失败: %v", err))
			os.Exit(1)
		}
	} else {
		var err error
		pyCmd, err = startPythonAPI(root)
		if err != nil {
			fail(fmt.Sprintf("启动 Python 后端失败: %v", err))
			os.Exit(1)
		}
	}

	if !waitHealth(apiURL) {
		msg := "API 未就绪（8765）。请确认 Python 已安装，且端口未被占用。"
		if useGoAPI {
			msg = "Go API 未就绪。请确认 Python 与 run.py go-bridge 可用，且 8765 端口未被占用。"
		}
		fail(msg)
		shutdown(pyCmd)
		os.Exit(1)
	}

	headless := os.Getenv("PIGEON_HEADLESS") == "1" || os.Getenv("PIGEON_API_ONLY") == "1"

	var wg sync.WaitGroup
	if pyCmd != nil {
		wg.Add(1)
		go func() {
			defer wg.Done()
			_ = pyCmd.Wait()
		}()
	}

	if headless {
		fmt.Fprintf(os.Stderr, "[pigeon-feige] headless API on %s\n", apiURL)
		waitExit(pyCmd)
		wg.Wait()
		return
	}

	useBrowser := os.Getenv("PIGEON_USE_BROWSER") == "1"
	if useBrowser {
		_ = openBrowser(apiURL)
		waitExit(pyCmd)
		wg.Wait()
		return
	}

	keepAPI := os.Getenv("PIGEON_KEEP_API_ON_WEBVIEW_EXIT") == "1"
	webviewDone := make(chan struct{})
	var webviewErr error
	go func() {
		defer close(webviewDone)
		webviewErr = runWebView(apiURL)
	}()

	sig := make(chan os.Signal, 1)
	signal.Notify(sig, os.Interrupt, syscall.SIGTERM)

	select {
	case <-webviewDone:
		if webviewErr != nil {
			fmt.Fprintf(os.Stderr, "[pigeon-feige] WebView exit: %v\n", webviewErr)
		} else {
			fmt.Fprintf(os.Stderr, "[pigeon-feige] WebView closed, shutting down API\n")
		}
		if keepAPI && webviewErr != nil {
			fmt.Fprintf(os.Stderr, "[pigeon-feige] PIGEON_KEEP_API_ON_WEBVIEW_EXIT=1, API stays up\n")
			waitExit(pyCmd)
			wg.Wait()
			return
		}
	case <-sig:
		fmt.Fprintf(os.Stderr, "[pigeon-feige] signal received, shutting down API\n")
	}

	shutdown(pyCmd)
	diagnosePort8765()
	wg.Wait()
	os.Exit(0)
}

func runWebView(url string) (err error) {
	defer func() {
		if r := recover(); r != nil {
			fmt.Fprintf(os.Stderr, "[pigeon-feige] WebView panic: %v\n", r)
			err = fmt.Errorf("webview panic: %v", r)
		}
	}()
	fmt.Fprintf(os.Stderr, "[pigeon-feige] WebView starting\n")
	if err := openWebView(url); err != nil {
		fmt.Fprintf(os.Stderr, "[pigeon-feige] WebView start failed: %v\n", err)
		fail("桌面窗口（WebView2）启动失败。")
		return err
	}
	fmt.Fprintf(os.Stderr, "[pigeon-feige] WebView closed\n")
	return nil
}

func startGoAPI(root string) error {
	bridge.NewClient(root).CleanupNodes("exe_boot")
	ln, err := net.Listen("tcp", "127.0.0.1:8765")
	if err != nil {
		return fmt.Errorf("bind :8765: %w", err)
	}
	apiListener = ln
	goAPIServer = api.NewServer(root)
	goAPIServer.StartBackgroundPrepare()
	httpServer = &http.Server{Handler: goAPIServer.Handler()}
	go func() {
		fmt.Fprintf(os.Stderr, "[pigeon-feige] Go API http://127.0.0.1:8765 (Python bridge worker)\n")
		if err := httpServer.Serve(apiListener); err != nil && err != http.ErrServerClosed {
			fmt.Fprintf(os.Stderr, "API server error: %v\n", err)
		}
	}()
	return nil
}

func shutdown(pyCmd *exec.Cmd) {
	if httpServer != nil {
		ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
		_ = httpServer.Shutdown(ctx)
		cancel()
		httpServer = nil
		apiListener = nil
	} else if apiListener != nil {
		_ = apiListener.Close()
		apiListener = nil
	}
	if goAPIServer != nil {
		srv := goAPIServer
		goAPIServer = nil
		done := make(chan struct{})
		go func() {
			srv.Close()
			close(done)
		}()
		select {
		case <-done:
		case <-time.After(5 * time.Second):
			fmt.Fprintf(os.Stderr, "[pigeon-feige] bridge close timeout\n")
		}
	}
	if pyCmd != nil && pyCmd.Process != nil {
		_ = pyCmd.Process.Kill()
	}
}

func diagnosePort8765() {
	client := &http.Client{Timeout: 500 * time.Millisecond}
	resp, err := client.Get(strings.TrimSuffix(apiURL, "/") + "/api/health")
	if err != nil {
		return
	}
	resp.Body.Close()
	fmt.Fprintf(os.Stderr, "[pigeon-feige] WARN: :8765 still accepting after shutdown\n")
	out, _ := exec.Command("netstat", "-ano").CombinedOutput()
	for _, line := range strings.Split(string(out), "\n") {
		if strings.Contains(line, ":8765") && strings.Contains(line, "LISTENING") {
			fmt.Fprintf(os.Stderr, "[pigeon-feige] %s\n", strings.TrimSpace(line))
		}
	}
}

func startPythonAPI(root string) (*exec.Cmd, error) {
	py := findPython(root)
	runPy := filepath.Join(root, "run.py")
	cmd := exec.Command(py, runPy, "serve-api", "--host", "127.0.0.1", "--port", "8765")
	execwin.Configure(cmd)
	cmd.Dir = root
	cmd.Env = append(os.Environ(), "PIGEON_STANDALONE=1")
	if isDevMode() {
		cmd.Stdout = os.Stdout
		cmd.Stderr = os.Stderr
	}
	if err := cmd.Start(); err != nil {
		return nil, err
	}
	return cmd, nil
}

func openWebView(url string) error {
	w, err := gowebview.New(&gowebview.Config{
		URL: url,
		WindowConfig: &gowebview.WindowConfig{
			Title: "抖店 AI 客服工作台",
			Size:  &gowebview.Point{X: 1440, Y: 900},
		},
		TransportConfig: &gowebview.TransportConfig{
			IgnoreNetworkIsolation: true,
		},
	})
	if err != nil {
		return fmt.Errorf("webview2: %w", err)
	}
	defer w.Destroy()
	w.Run()
	return nil
}

func waitExit(cmd *exec.Cmd) {
	sig := make(chan os.Signal, 1)
	signal.Notify(sig, os.Interrupt, syscall.SIGTERM)
	<-sig
	shutdown(cmd)
}

func waitHealth(url string) bool {
	client := &http.Client{Timeout: 2 * time.Second}
	healthURL := strings.TrimSuffix(url, "/") + "/api/health"
	for i := 0; i < 40; i++ {
		resp, err := client.Get(healthURL)
		if err == nil {
			resp.Body.Close()
			if resp.StatusCode == 200 {
				return true
			}
		}
		time.Sleep(250 * time.Millisecond)
	}
	return false
}

func openBrowser(url string) error {
	return exec.Command("cmd", "/c", "start", "", url).Start()
}

func isDevMode() bool {
	return os.Getenv("PIGEON_DEV") == "1"
}

func findProjectRoot() string {
	if env := os.Getenv("PIGEON_PROJECT_ROOT"); env != "" {
		return env
	}
	if env := os.Getenv("PIGEON_ROOT"); env != "" {
		return env
	}
	exe, err := os.Executable()
	if err == nil {
		exeDir := filepath.Dir(exe)
		if _, err := os.Stat(filepath.Join(exeDir, "run.py")); err == nil {
			return exeDir
		}
		parent := filepath.Dir(exeDir)
		if _, err := os.Stat(filepath.Join(parent, "run.py")); err == nil {
			return parent
		}
		for d := exeDir; ; d = filepath.Dir(d) {
			if _, err := os.Stat(filepath.Join(d, "run.py")); err == nil {
				return d
			}
			if filepath.Dir(d) == d {
				break
			}
		}
	}
	cwd, _ := os.Getwd()
	if _, err := os.Stat(filepath.Join(cwd, "run.py")); err == nil {
		return cwd
	}
	return cwd
}

func findPython(root string) string {
	if env := os.Getenv("PIGEON_PYTHON"); env != "" {
		return env
	}
	for _, rel := range []string{
		"runtime/python/python.exe",
		"runtime/python3/python.exe",
	} {
		p := filepath.Join(root, rel)
		if st, err := os.Stat(p); err == nil && !st.IsDir() {
			return p
		}
	}
	for _, name := range []string{"python", "python3", "py"} {
		if _, err := exec.LookPath(name); err == nil {
			return name
		}
	}
	return "python"
}

func fail(msg string) {
	fmt.Fprintf(os.Stderr, "[pigeon-feige] %s\n", msg)
	showMessageBox("抖店 AI 客服工作台", msg)
}

func showMessageBox(title, message string) {
	user32 := syscall.NewLazyDLL("user32.dll")
	messageBoxW := user32.NewProc("MessageBoxW")
	t, err1 := syscall.UTF16PtrFromString(title)
	m, err2 := syscall.UTF16PtrFromString(message)
	if err1 != nil || err2 != nil {
		return
	}
	_, _, _ = messageBoxW.Call(0, uintptr(unsafe.Pointer(m)), uintptr(unsafe.Pointer(t)), 0x10)
}
