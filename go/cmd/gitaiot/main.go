package main

import (
	"os"

	"github.com/Hieuslecong/labops_control_center/go/internal/gitaiotcli"
)

func main() {
	os.Exit(gitaiotcli.Run(os.Args[1:], "gitaiot", os.Stdout, os.Stderr))
}
