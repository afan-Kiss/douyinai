//go:build windows

package execwin

import (
	"os/exec"
	"syscall"
)

const createNoWindow = 0x08000000

// Configure hides the console window for child processes on Windows.
func Configure(cmd *exec.Cmd) {
	if cmd == nil {
		return
	}
	cmd.SysProcAttr = &syscall.SysProcAttr{
		HideWindow:    true,
		CreationFlags: createNoWindow,
	}
}
