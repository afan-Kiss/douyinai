//go:build !windows

package execwin

import "os/exec"

func Configure(cmd *exec.Cmd) {}
