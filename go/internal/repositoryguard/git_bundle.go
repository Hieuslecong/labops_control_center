package repositoryguard

import (
	"context"
	"errors"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
)

const approvedBundleRef = "refs/heads/aiotgit-approved"

// trustedGitCommand gives Git a deliberately small environment. Repository
// content is data only: system/global config, credential helpers, hooks and
// caller-controlled Git environment variables are not inherited.
func trustedGitCommand(ctx context.Context, args ...string) *exec.Cmd {
	cmd := exec.CommandContext(ctx, "git", args...)
	cmd.Env = []string{
		"PATH=/usr/bin:/bin",
		"HOME=/nonexistent",
		"GIT_CONFIG_NOSYSTEM=1",
		"GIT_CONFIG_SYSTEM=/dev/null",
		"GIT_CONFIG_GLOBAL=/dev/null",
		"GIT_CONFIG_COUNT=0",
		"GIT_TERMINAL_PROMPT=0",
		"GIT_ASKPASS=/bin/false",
		"SSH_ASKPASS=/bin/false",
	}
	return cmd
}

// createGitBundle packages exactly the approved commit as a portable Git
// repository. The bundle contains no user-controlled Git config or hooks.
func createGitBundle(ctx context.Context, gitDir, bundlePath, commitSHA string) error {
	if !validCommitSHA(commitSHA) {
		return errors.New("approved Git bundle commit is invalid")
	}
	parent := filepath.Dir(bundlePath)
	canonicalParent, err := filepath.EvalSymlinks(parent)
	if err != nil || canonicalParent != parent {
		return errors.New("Git bundle parent directory is not canonical")
	}
	if info, err := os.Lstat(bundlePath); err == nil || !errors.Is(err, os.ErrNotExist) {
		if err == nil && info.Mode().IsRegular() {
			return errors.New("Git bundle destination already exists")
		}
		return errors.New("Git bundle destination is not a fresh regular-file path")
	}

	update := trustedGitCommand(ctx, "-C", gitDir, "update-ref", approvedBundleRef, commitSHA)
	if output, err := update.CombinedOutput(); err != nil {
		return fmt.Errorf("pin approved Git commit: %w: %s", err, output)
	}
	create := trustedGitCommand(ctx, "-C", gitDir, "bundle", "create", bundlePath, approvedBundleRef)
	if output, err := create.CombinedOutput(); err != nil {
		return fmt.Errorf("create approved Git bundle: %w: %s", err, output)
	}
	info, err := os.Lstat(bundlePath)
	if err != nil || !info.Mode().IsRegular() || info.Size() <= 0 || info.Size() > MaxRepoSizeBytes {
		return errors.New("created Git bundle is missing or exceeds size limit")
	}
	verify := trustedGitCommand(ctx, "-C", gitDir, "bundle", "verify", bundlePath)
	if output, err := verify.CombinedOutput(); err != nil {
		return fmt.Errorf("verify approved Git bundle: %w: %s", err, output)
	}
	return nil
}
