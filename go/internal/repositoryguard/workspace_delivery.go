//go:build linux

package repositoryguard

import (
	"context"
	"fmt"
	"io/fs"
	"os"
	"path/filepath"
	"strings"

	"golang.org/x/sys/unix"
)

// WorkspaceDelivery is derived from local trusted Docker metadata.
type WorkspaceDelivery struct {
	Root string
	UID  int
	GID  int
}

type WorkspaceResolver func(context.Context, string, string, string, string) (WorkspaceDelivery, error)

func deliverToWorkspace(ctx context.Context, artifact, digest, commit, checkoutRef, originURL, relative string, workspace WorkspaceDelivery) error {
	if !filepath.IsLocal(relative) || relative == "." || strings.ContainsAny(relative, "\\\x00") {
		return fmt.Errorf("invalid relative workspace destination")
	}
	for _, part := range strings.Split(relative, "/") {
		if part == ".." || part == ".ssh" || part == ".git" {
			return fmt.Errorf("forbidden workspace destination component")
		}
	}
	relative = filepath.Clean(relative)
	if relative == "." || workspace.UID < 0 || workspace.GID < 0 {
		return fmt.Errorf("invalid workspace delivery identity or destination")
	}

	parentPath := filepath.Join(workspace.Root, filepath.Dir(relative))
	parentFD, err := unix.Openat2(unix.AT_FDCWD, parentPath, &unix.OpenHow{
		Flags: unix.O_DIRECTORY | unix.O_PATH | unix.O_CLOEXEC, Resolve: unix.RESOLVE_NO_SYMLINKS,
	})
	if err != nil {
		return fmt.Errorf("cannot open destination parent: %w", err)
	}
	defer unix.Close(parentFD)
	var existing unix.Stat_t
	if err := unix.Fstatat(parentFD, filepath.Base(relative), &existing, unix.AT_SYMLINK_NOFOLLOW); err != unix.ENOENT {
		return fmt.Errorf("workspace destination already exists or cannot be verified")
	}

	// Stage under an operator-owned directory on the workspace filesystem.
	stageBase := os.Getenv("LABOPS_REPOSITORY_DELIVERY_STAGING")
	if !filepath.IsAbs(stageBase) || LstatNoSymlinkChainFromRoot(stageBase) != nil {
		return fmt.Errorf("repository delivery staging directory is not configured")
	}
	info, err := os.Lstat(stageBase)
	if err != nil || !info.IsDir() || info.Mode().Perm() != 0700 {
		return fmt.Errorf("repository delivery staging directory must be private (0700)")
	}
	var baseStat, parentStat unix.Stat_t
	if unix.Lstat(stageBase, &baseStat) != nil || unix.Fstat(parentFD, &parentStat) != nil ||
		int(baseStat.Uid) != os.Geteuid() || baseStat.Dev != parentStat.Dev {
		return fmt.Errorf("repository staging must be agent-owned and on the workspace filesystem")
	}
	stage, err := os.MkdirTemp(stageBase, "delivery-")
	if err != nil {
		return err
	}
	defer os.RemoveAll(stage)
	repo := filepath.Join(stage, "repository")
	if err := DeliverArtifactStrict(ctx, artifact, digest, commit, repo, []string{stage}); err != nil {
		return err
	}
	if _, err := runGit(ctx, repo, "remote", "set-url", "origin", originURL); err != nil {
		return err
	}
	if branch, ok := strings.CutPrefix(checkoutRef, "refs/heads/"); ok {
		if branch != "main" {
			if _, err := runGit(ctx, repo, "branch", "-m", "--", branch); err != nil {
				return err
			}
		}
		if _, err := runGit(ctx, repo, "update-ref", "refs/remotes/origin/"+branch, commit); err != nil {
			return err
		}
		if _, err := runGit(ctx, repo, "branch", "--set-upstream-to=origin/"+branch, "--", branch); err != nil {
			return err
		}
	} else if _, err := runGit(ctx, repo, "checkout", "--detach", commit); err != nil {
		return err
	}

	var paths []string
	if err := filepath.WalkDir(repo, func(path string, _ fs.DirEntry, err error) error {
		if err != nil {
			return err
		}
		paths = append(paths, path)
		return nil
	}); err != nil {
		return err
	}
	published := false
	defer func() {
		if !published {
			// Restore parent ownership before children to allow cleanup of private staging.
			for _, path := range paths {
				_ = os.Lchown(path, os.Geteuid(), os.Getegid())
			}
		}
	}()
	// Assign directory ownership after its contents so traversal remains possible.
	for i := len(paths) - 1; i >= 0; i-- {
		if err := os.Lchown(paths[i], workspace.UID, workspace.GID); err != nil {
			return fmt.Errorf("cannot assign repository ownership: %w", err)
		}
	}
	// Verify the path still names the parent opened above before publishing.
	freshFD, err := unix.Openat2(unix.AT_FDCWD, parentPath, &unix.OpenHow{
		Flags: unix.O_DIRECTORY | unix.O_PATH | unix.O_CLOEXEC, Resolve: unix.RESOLVE_NO_SYMLINKS,
	})
	if err != nil {
		return fmt.Errorf("destination parent changed: %w", err)
	}
	defer unix.Close(freshFD)
	var first, current unix.Stat_t
	if unix.Fstat(parentFD, &first) != nil || unix.Fstat(freshFD, &current) != nil ||
		first.Dev != current.Dev || first.Ino != current.Ino {
		return fmt.Errorf("destination parent changed during delivery")
	}
	if err := unix.Renameat2(unix.AT_FDCWD, repo, parentFD, filepath.Base(relative), unix.RENAME_NOREPLACE); err != nil {
		return fmt.Errorf("cannot publish repository: %w", err)
	}
	published = true
	return nil
}
