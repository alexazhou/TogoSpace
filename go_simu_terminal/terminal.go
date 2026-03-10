package main

import (
	"io"
	"os"
	"os/exec"
	"sync"

	"github.com/creack/pty"
	headlessterm "github.com/danielgatis/go-headless-term"
)

// TerminalProcess manages a PTY child process and a headless virtual screen.
type TerminalProcess struct {
	term    *headlessterm.Terminal
	mu      sync.RWMutex // protects term: reader goroutine writes, HTTP handler reads
	ptmx    *os.File
	execCmd *exec.Cmd
	wmu     sync.Mutex // protects ptmx writes (SendInput concurrency)
	done    chan struct{}
	cols    int
	rows    int
}

// NewTerminalProcess creates a TerminalProcess with the given dimensions.
func NewTerminalProcess(cols, rows int) *TerminalProcess {
	return &TerminalProcess{
		cols: cols,
		rows: rows,
		done: make(chan struct{}),
	}
}

// Start launches the command in a PTY and begins reading output.
func (t *TerminalProcess) Start(cmd []string) error {
	t.execCmd = exec.Command(cmd[0], cmd[1:]...)

	t.execCmd.Env = append(os.Environ(),
		"TERM=xterm-256color",
		"COLORTERM=truecolor",
	)

	size := &pty.Winsize{
		Rows: uint16(t.rows),
		Cols: uint16(t.cols),
	}

	ptmx, err := pty.StartWithSize(t.execCmd, size)
	if err != nil {
		return err
	}
	t.ptmx = ptmx

	t.mu.Lock()
	t.term = headlessterm.New(headlessterm.WithSize(t.rows, t.cols))
	t.mu.Unlock()

	go t.readLoop()
	return nil
}

func (t *TerminalProcess) readLoop() {
	defer close(t.done)
	buf := make([]byte, 4096)
	for {
		n, err := t.ptmx.Read(buf)
		if n > 0 {
			t.mu.Lock()
			t.term.Write(buf[:n])
			t.mu.Unlock()
		}
		if err != nil {
			if err != io.EOF {
				// process exited or PTY closed — normal termination
			}
			return
		}
	}
}

// SendInput writes raw bytes to the PTY (concurrency-safe).
func (t *TerminalProcess) SendInput(data []byte) error {
	t.wmu.Lock()
	defer t.wmu.Unlock()
	_, err := t.ptmx.Write(data)
	return err
}

// Screenshot renders the current screen state to SVG bytes.
func (t *TerminalProcess) Screenshot() ([]byte, error) {
	t.mu.RLock()
	defer t.mu.RUnlock()
	svg := renderToSVG(t.term, t.cols, t.rows)
	return []byte(svg), nil
}

// Stop kills the child process and closes the PTY.
func (t *TerminalProcess) Stop() {
	if t.execCmd != nil && t.execCmd.Process != nil {
		t.execCmd.Process.Kill()
		t.execCmd.Wait()
	}
	if t.ptmx != nil {
		t.ptmx.Close()
	}
	select {
	case <-t.done:
	default:
	}
}
