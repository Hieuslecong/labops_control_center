package aiotgit

import (
	"context"
	"crypto/rand"
	"crypto/sha256"
	"encoding/hex"
	"errors"
	"flag"
	"fmt"
	"io"
	"net/url"
	"os"
	"os/exec"
	"path"
	"path/filepath"
	"regexp"
	"strings"
	"time"

	"golang.org/x/sys/unix"
)

const maxBundleBytes int64 = 500 * 1024 * 1024

var (
	gitBranchRE  = regexp.MustCompile(`^[A-Za-z0-9][A-Za-z0-9._/-]{0,254}$`)
	requestKeyRE = regexp.MustCompile(`^[a-f0-9]{32}$`)
)

type Config struct {
	Client        *Client
	WorkspaceRoot string
	PollInterval  time.Duration
	WaitLimit     time.Duration
	Context       context.Context
}

// Run implements only aiotgit clone and aiotgit status. It deliberately has no
// generic Git passthrough, so an unavailable Broker can never become git clone.
func Run(args []string, config Config, stdout, stderr io.Writer) int {
	if stdout == nil || stderr == nil {
		return 2
	}
	if len(args) == 0 || args[0] == "help" || args[0] == "--help" || args[0] == "-h" {
		printUsage(stdout)
		return 0
	}
	if config.Client == nil {
		fmt.Fprintln(stderr, "aiotgit: authenticated Repository Broker is not configured")
		return 2
	}
	switch args[0] {
	case "clone":
		return runClone(args[1:], config, stdout, stderr)
	case "status":
		return runStatus(args[1:], config, stdout, stderr)
	default:
		fmt.Fprintf(stderr, "aiotgit: unsupported command %q\n", args[0])
		printUsage(stderr)
		return 2
	}
}

func printUsage(w io.Writer) {
	fmt.Fprintln(w, "Usage:")
	fmt.Fprintln(w, "  aiotgit clone [-b BRANCH | --ref REF] URL [DESTINATION]")
	fmt.Fprintln(w, "  aiotgit status [REQUEST_ID]")
	fmt.Fprintln(w, "Use git for local Git operations such as status, add, commit, log, and branch.")
}

func runClone(args []string, config Config, stdout, stderr io.Writer) int {
	flags := flag.NewFlagSet("aiotgit clone", flag.ContinueOnError)
	flags.SetOutput(stderr)
	branch := flags.String("b", "", "approved branch to check out")
	flags.StringVar(branch, "branch", "", "approved branch to check out")
	requestedRef := flags.String("ref", "", "approved branch, tag, or commit")
	destinationFlag := flags.String("dest", "", "workspace-relative destination")
	if err := flags.Parse(args); err != nil {
		return 2
	}
	positional := flags.Args()
	if len(positional) < 1 || len(positional) > 2 || (*branch != "" && *requestedRef != "") {
		printUsage(stderr)
		return 2
	}
	repoURL := strings.TrimSpace(positional[0])
	ref := *requestedRef
	if *branch != "" {
		ref = *branch
	}
	if ref != "" && !validBranchOrRef(ref) {
		fmt.Fprintln(stderr, "aiotgit: branch or ref is invalid")
		return 2
	}
	destination := *destinationFlag
	if destination != "" && len(positional) == 2 {
		fmt.Fprintln(stderr, "aiotgit: specify destination either positionally or with --dest, not both")
		return 2
	}
	if len(positional) == 2 {
		destination = positional[1]
	}
	if destination == "" {
		var err error
		destination, err = defaultDestination(repoURL)
		if err != nil {
			fmt.Fprintf(stderr, "aiotgit: %v\n", err)
			return 2
		}
	}
	cleanDestination, err := validateWorkspaceDestination(destination)
	if err != nil {
		fmt.Fprintf(stderr, "aiotgit: %v\n", err)
		return 2
	}
	_, root, err := openWorkspaceRoot(config.WorkspaceRoot)
	if err != nil {
		fmt.Fprintf(stderr, "aiotgit: workspace root is unavailable: %v\n", err)
		return 2
	}
	defer root.Close()
	if _, err := root.Lstat(cleanDestination); err == nil {
		fmt.Fprintln(stderr, "aiotgit: destination already exists")
		return 2
	} else if !errors.Is(err, os.ErrNotExist) {
		fmt.Fprintf(stderr, "aiotgit: cannot inspect destination: %v\n", err)
		return 2
	}
	if err := ensureSafeParents(root, path.Dir(cleanDestination)); err != nil {
		fmt.Fprintf(stderr, "aiotgit: destination parent is unsafe: %v\n", err)
		return 2
	}

	idempotencyKey, err := newIdempotencyKey()
	if err != nil {
		fmt.Fprintf(stderr, "aiotgit: cannot create request identity: %v\n", err)
		return 2
	}
	ctx := config.context()
	request, err := config.Client.Submit(ctx, repoURL, ref, idempotencyKey)
	if err != nil {
		fmt.Fprintf(stderr, "aiotgit: request was not submitted: %v\n", err)
		return 1
	}
	fmt.Fprintf(stdout, "Repository request %s: %s\n", request.ID, request.State)
	request, err = waitForApproval(ctx, config.Client, request, config.PollInterval, config.WaitLimit)
	if err != nil {
		fmt.Fprintf(stderr, "aiotgit: request %s is not ready for delivery: %v\n", request.ID, err)
		return 1
	}
	if !isApprovedState(request.State) || !commitRE.MatchString(request.CommitSHA) || !sha256RE.MatchString(request.GitBundleDigest) {
		fmt.Fprintf(stderr, "aiotgit: request %s has no verified approved Git bundle\n", request.ID)
		return 1
	}
	if err := publishApprovedBundle(ctx, config.Client, request, root, cleanDestination, *branch); err != nil {
		fmt.Fprintf(stderr, "aiotgit: approved repository was not published: %v\n", err)
		return 1
	}
	fmt.Fprintf(stdout, "Approved repository %s delivered to %s at %s\n", request.ID, cleanDestination, request.CommitSHA)
	return 0
}

func runStatus(args []string, config Config, stdout, stderr io.Writer) int {
	if len(args) > 1 {
		fmt.Fprintln(stderr, "Usage: aiotgit status [REQUEST_ID]")
		return 2
	}
	ctx := config.context()
	var requests []RequestInfo
	if len(args) == 1 {
		request, err := config.Client.Get(ctx, args[0])
		if err != nil {
			fmt.Fprintf(stderr, "aiotgit: status unavailable: %v\n", err)
			return 1
		}
		requests = []RequestInfo{*request}
	} else {
		var err error
		requests, err = config.Client.List(ctx)
		if err != nil {
			fmt.Fprintf(stderr, "aiotgit: status unavailable: %v\n", err)
			return 1
		}
		if len(requests) == 0 {
			fmt.Fprintln(stdout, "No repository requests for this workspace.")
			return 0
		}
		requests = requests[:1] // Broker returns this workspace's newest request first.
	}
	for _, request := range requests {
		fmt.Fprintf(stdout, "Request: %s\nState: %s\nRepository: %s\nRef: %s\nCommit: %s\n", request.ID, request.State, request.RepoURL, request.RequestedRef, request.CommitSHA)
		if request.Error != "" {
			fmt.Fprintf(stdout, "Reason: %s\n", request.Error)
		}
	}
	return 0
}

func waitForApproval(ctx context.Context, client *Client, current *RequestInfo, pollEvery, waitLimit time.Duration) (*RequestInfo, error) {
	if isApprovedState(current.State) || isTerminalRequestState(current.State) || waitLimit <= 0 {
		return current, nil
	}
	if pollEvery <= 0 {
		pollEvery = 1500 * time.Millisecond
	}
	timer := time.NewTimer(waitLimit)
	defer timer.Stop()
	for {
		select {
		case <-ctx.Done():
			return current, ctx.Err()
		case <-timer.C:
			return current, errors.New("approval is still pending; use aiotgit status " + current.ID)
		case <-time.After(pollEvery):
			updated, err := client.Get(ctx, current.ID)
			if err != nil {
				return current, err
			}
			current = updated
			if isApprovedState(current.State) || isTerminalRequestState(current.State) {
				return current, nil
			}
		}
	}
}

func isApprovedState(state string) bool {
	switch state {
	case "AUTO_APPROVED", "MANUALLY_APPROVED", "APPROVED":
		return true
	default:
		return false
	}
}

func isTerminalRequestState(state string) bool {
	switch state {
	case "REJECTED", "FAILED", "CANCELLED", "EXPIRED", "DELIVERY_FAILED":
		return true
	default:
		return false
	}
}

func publishApprovedBundle(ctx context.Context, client *Client, request *RequestInfo, workspace *os.Root, destination, branch string) error {
	bundle, digest, commit, err := client.Bundle(ctx, request.ID)
	if err != nil {
		return err
	}
	defer bundle.Close()
	if digest != request.GitBundleDigest || commit != request.CommitSHA {
		return errors.New("bundle metadata does not match the approved request")
	}

	parentPath := path.Dir(destination)
	parent, err := workspace.OpenRoot(parentPath)
	if err != nil {
		return fmt.Errorf("open destination parent: %w", err)
	}
	defer parent.Close()
	stageName, err := randomStageName()
	if err != nil {
		return err
	}
	if err := parent.Mkdir(stageName, 0700); err != nil {
		return fmt.Errorf("create staging directory: %w", err)
	}
	stageExists := true
	defer func() {
		if stageExists {
			_ = parent.RemoveAll(stageName)
		}
	}()

	stageDirectory, err := parent.Open(stageName)
	if err != nil {
		return fmt.Errorf("open staging directory: %w", err)
	}
	defer stageDirectory.Close()
	stageInfo, err := stageDirectory.Stat()
	if err != nil || !stageInfo.IsDir() {
		return errors.New("staging destination is not a directory")
	}
	stagePath := "/proc/self/fd/3"
	if filepath.Separator != '/' {
		return errors.New("secure staged Git delivery requires Linux")
	}

	bundleFile, err := os.CreateTemp("", "aiotgit-approved-*.bundle")
	if err != nil {
		return fmt.Errorf("create temporary approved bundle: %w", err)
	}
	bundlePath := bundleFile.Name()
	defer func() {
		_ = bundleFile.Close()
		_ = os.Remove(bundlePath)
	}()
	if err := bundleFile.Chmod(0600); err != nil {
		return err
	}
	hasher := sha256.New()
	size, err := io.Copy(io.MultiWriter(bundleFile, hasher), io.LimitReader(bundle, maxBundleBytes+1))
	if err != nil {
		return fmt.Errorf("read approved Git bundle: %w", err)
	}
	if size <= 0 || size > maxBundleBytes {
		return errors.New("approved Git bundle is empty or exceeds size limit")
	}
	if hex.EncodeToString(hasher.Sum(nil)) != digest {
		return errors.New("approved Git bundle digest mismatch")
	}
	if err := bundleFile.Sync(); err != nil {
		return fmt.Errorf("sync approved Git bundle: %w", err)
	}
	if _, err := bundleFile.Seek(0, io.SeekStart); err != nil {
		return err
	}
	if err := bundleFile.Close(); err != nil {
		return err
	}
	if err := runGitInStage(ctx, stageDirectory, "clone", "--no-hardlinks", "--branch", "aiotgit-approved", "--", bundlePath, stagePath); err != nil {
		return fmt.Errorf("clone approved bundle: %w", err)
	}
	if branch != "" {
		if err := runGitInStage(ctx, stageDirectory, "-C", stagePath, "branch", "-m", "aiotgit-approved", branch); err != nil {
			return fmt.Errorf("set requested local branch: %w", err)
		}
	}
	if err := runGitInStage(ctx, stageDirectory, "-C", stagePath, "remote", "remove", "origin"); err != nil {
		return fmt.Errorf("remove temporary bundle remote: %w", err)
	}
	actualCommit, err := runGitOutputInStage(ctx, stageDirectory, "-C", stagePath, "rev-parse", "HEAD")
	if err != nil {
		return fmt.Errorf("verify delivered Git HEAD: %w", err)
	}
	if strings.TrimSpace(actualCommit) != request.CommitSHA {
		return errors.New("delivered Git HEAD does not match approved commit")
	}
	status, err := runGitOutputInStage(ctx, stageDirectory, "-C", stagePath, "status", "--porcelain")
	if err != nil {
		return fmt.Errorf("verify delivered Git working tree: %w", err)
	}
	if strings.TrimSpace(status) != "" {
		return errors.New("delivered Git working tree is not clean")
	}
	if err := stageDirectory.Sync(); err != nil {
		return fmt.Errorf("sync staged Git repository: %w", err)
	}
	if _, err := workspace.Lstat(destination); err == nil {
		return errors.New("destination appeared while repository was being prepared")
	} else if !errors.Is(err, os.ErrNotExist) {
		return fmt.Errorf("recheck destination: %w", err)
	}
	parentDirectory, err := parent.Open(".")
	if err != nil {
		return fmt.Errorf("open staged destination parent: %w", err)
	}
	defer parentDirectory.Close()
	if err := unix.Renameat2(int(parentDirectory.Fd()), stageName, int(parentDirectory.Fd()), path.Base(destination), unix.RENAME_NOREPLACE); err != nil {
		return fmt.Errorf("publish staged Git repository: %w", err)
	}
	stageExists = false
	return nil
}

func runGitInStage(ctx context.Context, stage *os.File, args ...string) error {
	_, err := runGitOutputInStage(ctx, stage, args...)
	return err
}

func runGitOutputInStage(ctx context.Context, stage *os.File, args ...string) (string, error) {
	git := "/usr/bin/git"
	if _, err := os.Stat(git); err != nil {
		git = "/bin/git"
		if _, err := os.Stat(git); err != nil {
			return "", errors.New("trusted system Git binary was not found")
		}
	}
	command := exec.CommandContext(ctx, git, append([]string{"-c", "core.hooksPath=/dev/null", "-c", "protocol.file.allow=always"}, args...)...)
	command.Env = []string{
		"PATH=/usr/bin:/bin", "HOME=/nonexistent", "GIT_CONFIG_NOSYSTEM=1", "GIT_CONFIG_SYSTEM=/dev/null",
		"GIT_CONFIG_GLOBAL=/dev/null", "GIT_CONFIG_COUNT=0", "GIT_TERMINAL_PROMPT=0", "GIT_ASKPASS=/bin/false", "SSH_ASKPASS=/bin/false",
	}
	command.ExtraFiles = []*os.File{stage}
	output, err := command.CombinedOutput()
	if err != nil {
		if len(output) > 4096 {
			output = output[:4096]
		}
		return string(output), fmt.Errorf("Git command failed: %w: %s", err, strings.TrimSpace(string(output)))
	}
	if len(output) > 64<<10 {
		return "", errors.New("Git command output exceeded size limit")
	}
	return string(output), nil
}

func openWorkspaceRoot(raw string) (string, *os.Root, error) {
	if strings.TrimSpace(raw) == "" {
		return "", nil, errors.New("workspace root is not configured")
	}
	abs, err := filepath.Abs(raw)
	if err != nil {
		return "", nil, err
	}
	canonical, err := filepath.EvalSymlinks(abs)
	if err != nil || canonical != abs {
		return "", nil, errors.New("workspace root must be an existing canonical path")
	}
	info, err := os.Lstat(abs)
	if err != nil || !info.IsDir() || info.Mode()&os.ModeSymlink != 0 {
		return "", nil, errors.New("workspace root must be a real directory")
	}
	root, err := os.OpenRoot(abs)
	if err != nil {
		return "", nil, err
	}
	return abs, root, nil
}

func validateWorkspaceDestination(raw string) (string, error) {
	if raw == "" || filepath.IsAbs(raw) || strings.ContainsRune(raw, '\\') || strings.ContainsRune(raw, 0) {
		return "", errors.New("destination must be a workspace-relative path")
	}
	clean := path.Clean(strings.ReplaceAll(raw, "\\", "/"))
	if clean == "." || clean == ".." || strings.HasPrefix(clean, "../") || strings.HasPrefix(clean, "/") {
		return "", errors.New("destination escapes the workspace")
	}
	for _, part := range strings.Split(clean, "/") {
		if part == "" || part == "." || part == ".." {
			return "", errors.New("destination contains an unsafe path component")
		}
	}
	return clean, nil
}

func ensureSafeParents(root *os.Root, parent string) error {
	if parent == "." {
		return nil
	}
	current := ""
	for _, component := range strings.Split(parent, "/") {
		if component == "" || component == "." || component == ".." {
			return errors.New("unsafe parent path")
		}
		if current == "" {
			current = component
		} else {
			current += "/" + component
		}
		info, err := root.Lstat(current)
		if errors.Is(err, os.ErrNotExist) {
			if err := root.Mkdir(current, 0700); err != nil && !errors.Is(err, os.ErrExist) {
				return err
			}
			info, err = root.Lstat(current)
		}
		if err != nil {
			return err
		}
		if !info.IsDir() || info.Mode()&os.ModeSymlink != 0 {
			return errors.New("parent path contains a non-directory or symlink")
		}
		child, err := root.OpenRoot(current)
		if err != nil {
			return err
		}
		if err := child.Close(); err != nil {
			return err
		}
	}
	return nil
}

func defaultDestination(repoURL string) (string, error) {
	parsed, err := url.Parse(repoURL)
	if err != nil || parsed.Scheme != "https" || parsed.Hostname() == "" || parsed.User != nil || parsed.RawQuery != "" || parsed.Fragment != "" {
		return "", errors.New("repository URL is invalid")
	}
	name := path.Base(strings.TrimSuffix(strings.Trim(parsed.Path, "/"), ".git"))
	return validateWorkspaceDestination(name)
}

func validBranchOrRef(ref string) bool {
	if !gitBranchRE.MatchString(ref) || strings.Contains(ref, "..") || strings.Contains(ref, "//") || strings.Contains(ref, "@{") || strings.HasSuffix(ref, ".") || strings.HasSuffix(ref, ".lock") || strings.HasPrefix(ref, "-") {
		return false
	}
	for _, component := range strings.Split(ref, "/") {
		if component == "" || component == "." || component == ".." || strings.HasPrefix(component, ".") || strings.HasSuffix(component, ".lock") {
			return false
		}
	}
	return true
}

func newIdempotencyKey() (string, error) {
	var key [16]byte
	if _, err := rand.Read(key[:]); err != nil {
		return "", err
	}
	encoded := hex.EncodeToString(key[:])
	if !requestKeyRE.MatchString(encoded) {
		return "", errors.New("unable to create a valid idempotency key")
	}
	return encoded, nil
}

func randomStageName() (string, error) {
	var token [16]byte
	if _, err := rand.Read(token[:]); err != nil {
		return "", err
	}
	return ".aiotgit-stage-" + hex.EncodeToString(token[:]), nil
}

func (c Config) context() context.Context {
	if c.Context != nil {
		return c.Context
	}
	return context.Background()
}
