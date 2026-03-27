package main

import (
	"fmt"
	"os"

	"github.com/gkze/ghawfr/workerproto"
)

func main() {
	if err := run(os.Args[1:]); err != nil {
		fmt.Fprintf(os.Stderr, "ghawfr-worker: %v\n", err)
		os.Exit(1)
	}
}

func run(args []string) error {
	if len(args) == 0 || args[0] == "-h" || args[0] == "--help" || args[0] == "help" {
		printUsage()
		return nil
	}
	switch args[0] {
	case "serve":
		if len(args) != 2 || args[1] != "--stdio" {
			return fmt.Errorf("serve requires --stdio")
		}
		return (workerproto.Server{WorkerName: "ghawfr-worker"}).ServeStdio(os.Stdin, os.Stdout)
	default:
		return fmt.Errorf("unknown command %q", args[0])
	}
}

func printUsage() {
	fmt.Println("ghawfr-worker <command>")
	fmt.Println()
	fmt.Println("Commands:")
	fmt.Println("  serve --stdio   Serve the ghawfr worker protocol over stdin/stdout")
}
