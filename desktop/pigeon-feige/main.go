package main

import (
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

var goAPIServer *api.Server

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
	if !openWebView(apiURL) {
		fail("桌面窗口（WebView2）启动失败。请安装 Microsoft Edge WebView2 运行时后重试。")
		shutdown(pyCmd)
		os.Exit(1)
	}

	waitExit(pyCmd)
	wg.Wait()
}

func startGoAPI(root string) error {
	bridge.NewClient(root).CleanupNodes("exe_boot")
	ln, err := net.Listen("tcp", "127.0.0.1:8765")
	if err != nil {
		return fmt.Errorf("bind :8765: %w", err)
	}
	goAPIServer = api.NewServer(root)
	goAPIServer.StartBackgroundPrepare()
	go func() {
		fmt.Fprintf(os.Stderr, "[pigeon-feige] Go API http://127.0.0.1:8765 (Python bridge worker)\n")
		if err := http.Serve(ln, goAPIServer.Handler()); err != nil && err != http.ErrServerClosed {
			fmt.Fprintf(os.Stderr, "API server error: %v\n", err)
		}
	}()
	return nil
}

func shutdown(pyCmd *exec.Cmd) {
	if goAPIServer != nil {
		goAPIServer.Close()
		goAPIServer = nil
	}
	if pyCmd != nil && pyCmd.Process != nil {
		_ = pyCmd.Process.Kill()
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

func openWebView(url string) bool {
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
		fmt.Fprintf(os.Stderr, "WebView2 启动失败: %v\n", err)
		return false
	}
	defer w.Destroy()
	w.Run()
	return true
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
