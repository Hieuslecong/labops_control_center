package main

import (
	"os"
	"path/filepath"

	"github.com/Hieuslecong/labops_control_center/go/internal/gitaiotcli"
)

func main() {
	os.Exit(gitaiotcli.Run(os.Args[1:], filepath.Base(os.Args[0]), os.Stdout, os.Stderr))
}
