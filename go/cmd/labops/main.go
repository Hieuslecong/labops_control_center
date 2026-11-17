package main

import (
	"fmt"
	"io"
	"os"
)

func main() {
	os.Exit(run(os.Args[1:], os.Stdout, os.Stderr))
}

func run(args []string, stdout, stderr io.Writer) int {
	if len(args) == 0 || args[0] == "help" || args[0] == "--help" || args[0] == "-h" {
		printUsage(stdout)
		return 0
	}

	switch args[0] {
	case "version", "--version", "-v":
		fmt.Fprintln(stdout, "LabOps CLI v2.0.0")
		return 0
	case "clone", "repo":
		fmt.Fprintln(stderr, "Legacy repository commands are disabled. Use aiotgit clone and aiotgit status; use git for local Git operations.")
		return 2
	default:
		fmt.Fprintf(stderr, "Unsupported command: %s\n", args[0])
		printUsage(stderr)
		return 2
	}
}

func printUsage(w io.Writer) {
	fmt.Fprintln(w, "LabOps Control Center CLI")
	fmt.Fprintln(w, "  labops version")
	fmt.Fprintln(w, "  aiotgit clone [-b BRANCH | --ref REF] URL [DESTINATION]")
	fmt.Fprintln(w, "  aiotgit status [REQUEST_ID]")
	fmt.Fprintln(w, "Use git for local Git operations such as status, add, commit, log, and branch.")
}
