package main

import (
	"flag"
	"fmt"
	"os"
)

func main() {
	port := flag.Int("port", 8888, "HTTP listen port")
	cols := flag.Int("cols", 140, "Terminal width in columns")
	rows := flag.Int("rows", 36, "Terminal height in rows")
	snapshot := flag.String("snapshot", "", "Take a snapshot and exit (e.g. out.png or out.svg)")
	scale := flag.Float64("scale", 1.0, "Scale for the snapshot (e.g. 2.0 for high DPI)")
	fontAscii := flag.String("font-ascii", "", "Path to custom ASCII font (.ttf)")
	fontCJK := flag.String("font-cjk", "", "Path to custom CJK font (.ttf or .ttc)")
	flag.Parse()

	cmd := flag.Args()
	if len(cmd) == 0 {
		fmt.Fprintln(os.Stderr, "Usage: simu_terminal_go [--port 8888] [--cols 140] [--rows 36] [--snapshot out.png] [--scale 2.0] [--font-ascii mono.ttf] [--font-cjk cjk.ttf] -- <command> [args...]")
		os.Exit(1)
	}

	if *snapshot != "" {
		runSnapshot(cmd, *snapshot, *cols, *rows, *scale, *fontAscii, *fontCJK)
		return
	}

	serve(cmd, *port, *cols, *rows, *fontAscii, *fontCJK)
}

func runSnapshot(cmd []string, filename string, cols, rows int, scale float64, fontAscii, fontCJK string) {
	process := NewTerminalProcess(cols, rows)
	process.fontAscii = fontAscii
	process.fontCJK = fontCJK
	if err := process.Start(cmd); err != nil {
		fmt.Fprintf(os.Stderr, "Failed to start process: %v\n", err)
		os.Exit(1)
	}

	// Wait for process to exit
	<-process.done

	if err := process.SaveScreenshot(filename, scale); err != nil {
		fmt.Fprintf(os.Stderr, "Failed to save snapshot: %v\n", err)
		os.Exit(1)
	}
	fmt.Printf("Snapshot saved to %s (scale: %.1f)\n", filename, scale)
}
