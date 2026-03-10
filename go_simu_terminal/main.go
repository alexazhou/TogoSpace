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
	flag.Parse()

	cmd := flag.Args()
	if len(cmd) == 0 {
		fmt.Fprintln(os.Stderr, "Usage: simu_terminal_go [--port 8888] [--cols 140] [--rows 36] -- <command> [args...]")
		os.Exit(1)
	}

	serve(cmd, *port, *cols, *rows)
}
