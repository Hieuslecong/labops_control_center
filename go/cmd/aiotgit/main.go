package main

import (
	"context"
	"errors"
	"fmt"
	"io"
	"net/http"
	"os"
	"os/signal"
	"path/filepath"
	"strings"
	"syscall"
	"time"

	"github.com/Hieuslecong/labops_control_center/go/internal/aiotgit"
	"golang.org/x/sys/unix"
)

func main() {
	os.Exit(run(os.Args[1:], os.Stdout, os.Stderr))
}

func run(args []string, stdout, stderr *os.File) int {
	if len(args) == 0 || args[0] == "help" || args[0] == "--help" || args[0] == "-h" {
		return aiotgit.Run(args, aiotgit.Config{}, stdout, stderr)
	}
	baseURL := os.Getenv("AIOTGIT_BROKER_URL")
	workspaceRoot := os.Getenv("AIOTGIT_WORKSPACE_ROOT")
	if baseURL == "" || workspaceRoot == "" {
		fmt.Fprintln(stderr, "aiotgit: AIOTGIT_BROKER_URL and AIOTGIT_WORKSPACE_ROOT must be provisioned")
		return 2
	}
	tokenPath := os.Getenv("AIOTGIT_TOKEN_FILE")
	if tokenPath == "" {
		home, err := os.UserHomeDir()
		if err != nil || home == "" {
			fmt.Fprintln(stderr, "aiotgit: cannot determine the current user's home directory")
			return 2
		}
		tokenPath = filepath.Join(home, ".config", "aiotgit", "token")
	}
	token, err := readScopedToken(tokenPath)
	if err != nil {
		fmt.Fprintf(stderr, "aiotgit: scoped workspace token unavailable: %v\n", err)
		return 2
	}
	client, err := aiotgit.NewClient(baseURL, token, &http.Client{Timeout: 2 * time.Minute})
	if err != nil {
		fmt.Fprintf(stderr, "aiotgit: Broker configuration is invalid: %v\n", err)
		return 2
	}
	ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer stop()
	return aiotgit.Run(args, aiotgit.Config{
		Client: client, WorkspaceRoot: workspaceRoot, Context: ctx,
		PollInterval: 1500 * time.Millisecond, WaitLimit: 10 * time.Minute,
	}, stdout, stderr)
}

func readScopedToken(path string) (string, error) {
	fd, err := unix.Open(path, unix.O_RDONLY|unix.O_CLOEXEC|unix.O_NOFOLLOW, 0)
	if err != nil {
		return "", err
	}
	file := os.NewFile(uintptr(fd), path)
	if file == nil {
		_ = unix.Close(fd)
		return "", errors.New("token file could not be opened")
	}
	defer file.Close()
	var info unix.Stat_t
	if err := unix.Fstat(fd, &info); err != nil {
		return "", err
	}
	if info.Mode&unix.S_IFMT != unix.S_IFREG || info.Mode&0077 != 0 {
		return "", errors.New("token file must be a regular file with mode 0600 or stricter")
	}
	if info.Uid != uint32(os.Getuid()) {
		return "", errors.New("token file must be owned by the current user")
	}
	data, err := io.ReadAll(io.LimitReader(file, (16<<10)+1))
	if err != nil {
		return "", err
	}
	if len(data) == 0 || len(data) > 16<<10 {
		return "", errors.New("token file is empty or too large")
	}
	token := strings.TrimSpace(string(data))
	if token == "" || strings.ContainsAny(token, " \t\r\n") {
		return "", errors.New("token file contains invalid characters")
	}
	return token, nil
}
