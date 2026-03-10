package main

import (
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"os"
	"os/signal"
	"strconv"
	"strings"
	"syscall"
)

// keyMap maps key names to their escape sequences, mirroring the Python version.
var keyMap = map[string][]byte{
	"up":       {0x1b, '[', 'A'},
	"down":     {0x1b, '[', 'B'},
	"left":     {0x1b, '[', 'D'},
	"right":    {0x1b, '[', 'C'},
	"enter":    {'\r'},
	"tab":      {'\t'},
	"esc":      {0x1b},
	"ctrl+a":   {0x01},
	"ctrl+b":   {0x02},
	"ctrl+c":   {0x03},
	"ctrl+d":   {0x04},
	"ctrl+e":   {0x05},
	"ctrl+f":   {0x06},
	"ctrl+q":   {0x11},
	"ctrl+r":   {0x12},
	"ctrl+s":   {0x13},
	"ctrl+u":   {0x15},
	"ctrl+w":   {0x17},
	"ctrl+z":   {0x1a},
}

func keyToBytes(key string) []byte {
	lower := strings.ToLower(key)
	if b, ok := keyMap[lower]; ok {
		return b
	}
	// Generic ctrl+<letter>
	if len(lower) == 6 && lower[:5] == "ctrl+" {
		ch := lower[5]
		if ch >= 'a' && ch <= 'z' {
			return []byte{ch - 'a' + 1}
		}
	}
	return []byte(key)
}

type inputRequest struct {
	Text *string `json:"text"`
	Key  *string `json:"key"`
}

func serve(cmd []string, port, cols, rows int, fontAscii, fontCJK string) {
	process := NewTerminalProcess(cols, rows)
	process.fontAscii = fontAscii
	process.fontCJK = fontCJK
	if err := process.Start(cmd); err != nil {
		fmt.Fprintf(os.Stderr, "Failed to start process: %v\n", err)
		os.Exit(1)
	}

	mux := http.NewServeMux()

	// POST /input
	mux.HandleFunc("/input", func(w http.ResponseWriter, r *http.Request) {
		if r.Method != http.MethodPost {
			http.Error(w, "Method not allowed", http.StatusMethodNotAllowed)
			return
		}
		body, err := io.ReadAll(r.Body)
		if err != nil {
			http.Error(w, "Failed to read body", http.StatusBadRequest)
			return
		}
		var req inputRequest
		if err := json.Unmarshal(body, &req); err != nil {
			http.Error(w, "Invalid JSON", http.StatusBadRequest)
			return
		}
		var data []byte
		switch {
		case req.Text != nil:
			data = []byte(*req.Text)
		case req.Key != nil:
			data = keyToBytes(*req.Key)
		default:
			http.Error(w, `Require "text" or "key" field`, http.StatusBadRequest)
			return
		}
		if err := process.SendInput(data); err != nil {
			http.Error(w, fmt.Sprintf("SendInput error: %v", err), http.StatusInternalServerError)
			return
		}
		w.Header().Set("Content-Type", "application/json")
		w.Write([]byte(`{"ok":true}`))
	})

	// GET /screenshot
	mux.HandleFunc("/screenshot", func(w http.ResponseWriter, r *http.Request) {
		if r.Method != http.MethodGet {
			http.Error(w, "Method not allowed", http.StatusMethodNotAllowed)
			return
		}

		scaleStr := r.URL.Query().Get("scale")
		scale, _ := strconv.ParseFloat(scaleStr, 64)
		if scale <= 0 {
			scale = 1.0
		}

		saveTo := r.URL.Query().Get("save")
		if saveTo != "" {
			if err := process.SaveScreenshot(saveTo, scale); err != nil {
				http.Error(w, fmt.Sprintf("Save error: %v", err), http.StatusInternalServerError)
				return
			}
			w.Header().Set("Content-Type", "application/json")
			w.Write([]byte(`{"ok":true,"saved":true}`))
			return
		}

		svgBytes, err := process.Screenshot()
		if err != nil {
			http.Error(w, fmt.Sprintf("Screenshot error: %v", err), http.StatusInternalServerError)
			return
		}
		w.Header().Set("Content-Type", "image/svg+xml")
		w.Write(svgBytes)
	})

	// GET /export?format=png|svg&filename=...&scale=1.0
	mux.HandleFunc("/export", func(w http.ResponseWriter, r *http.Request) {
		if r.Method != http.MethodGet {
			http.Error(w, "Method not allowed", http.StatusMethodNotAllowed)
			return
		}

		format := r.URL.Query().Get("format")
		filename := r.URL.Query().Get("filename")
		scaleStr := r.URL.Query().Get("scale")
		scale, _ := strconv.ParseFloat(scaleStr, 64)
		if scale <= 0 {
			scale = 1.0
		}

		if filename == "" {
			if format == "png" {
				filename = "screenshot.png"
			} else {
				filename = "screenshot.svg"
			}
		}

		var data []byte
		var err error
		var contentType string

		if format == "png" {
			data, err = process.ScreenshotPNG(scale)
			contentType = "image/png"
		} else {
			data, err = process.Screenshot()
			contentType = "image/svg+xml"
		}

		if err != nil {
			http.Error(w, fmt.Sprintf("Export error: %v", err), http.StatusInternalServerError)
			return
		}

		w.Header().Set("Content-Type", contentType)
		w.Header().Set("Content-Disposition", fmt.Sprintf(`attachment; filename="%s"`, filename))
		w.Write(data)
	})

	srv := &http.Server{
		Addr:    fmt.Sprintf("0.0.0.0:%d", port),
		Handler: mux,
	}

	// Graceful shutdown on SIGINT / SIGTERM
	sigCh := make(chan os.Signal, 1)
	signal.Notify(sigCh, syscall.SIGINT, syscall.SIGTERM)
	go func() {
		<-sigCh
		fmt.Fprintln(os.Stdout, "\nShutting down…")
		process.Stop()
		srv.Shutdown(context.Background())
	}()

	fmt.Printf("simu_terminal_go listening on http://0.0.0.0:%d\n", port)
	fmt.Printf("  cmd: %v\n", cmd)

	if err := srv.ListenAndServe(); err != nil && err != http.ErrServerClosed {
		fmt.Fprintf(os.Stderr, "HTTP server error: %v\n", err)
		os.Exit(1)
	}
}
