package docker

import (
	"context"
	"fmt"
	"net/url"
	"os"
	"path/filepath"
	"strconv"
	"strings"
)

const repositoryCredentialMount = "/run/labops/credentials"

func (c *Controller) prepareRepositoryCredentials(req ProvisionRequest, runtimeDir string) (string, error) {
	u, err := url.Parse(req.RepositoryAPIURL)
	if err != nil || u.Scheme != "https" || u.Hostname() == "" || u.User != nil ||
		u.RawQuery != "" || u.Fragment != "" || !strings.HasSuffix(strings.TrimRight(u.Path, "/"), "/api/v1/repositories") {
		return "", fmt.Errorf("invalid HTTPS repository API URL")
	}
	if req.NetworkIsolated || strings.TrimSpace(req.RepositoryToken) == "" || len(req.RepositoryToken) > 8192 {
		return "", fmt.Errorf("managed repository credentials require a connected workspace and a scoped token")
	}
	dir := filepath.Join(runtimeDir, "repository_credentials")
	if err := os.Mkdir(dir, 0755); err != nil && !os.IsExist(err) {
		return "", err
	}
	info, err := os.Lstat(dir)
	if err != nil || !info.IsDir() || info.Mode().Perm()&0022 != 0 {
		return "", fmt.Errorf("unsafe repository credential directory")
	}
	if err := os.WriteFile(filepath.Join(dir, "repository-api-url"), []byte(req.RepositoryAPIURL+"\n"), 0644); err != nil {
		return "", err
	}
	token, err := os.CreateTemp(dir, ".repository-token-")
	if err != nil {
		return "", err
	}
	defer os.Remove(token.Name())
	defer token.Close()
	if _, err := token.WriteString(req.RepositoryToken + "\n"); err != nil {
		return "", err
	}
	if err := token.Chown(req.UID, req.GID); err != nil {
		return "", fmt.Errorf("assign repository credential ownership: %w", err)
	}
	if err := token.Sync(); err != nil {
		return "", err
	}
	if err := token.Close(); err != nil {
		return "", err
	}
	if err := os.Rename(token.Name(), filepath.Join(dir, "repository-token")); err != nil {
		return "", err
	}
	return dir, nil
}

// ResolveRepositoryWorkspace verifies the live storage mount and registry identity.
func (c *Controller) ResolveRepositoryWorkspace(ctx context.Context, workspaceID, memberID, username, nodeID string) (string, int, int, error) {
	if c == nil || c.registry == nil || workspaceID == "" || memberID == "" || username == "" || nodeID != c.nodeID {
		return "", 0, 0, fmt.Errorf("invalid repository workspace identity")
	}
	rec, err := c.registry.GetByWorkspaceID(workspaceID)
	if err != nil {
		return "", 0, 0, err
	}
	if rec.MemberID != memberID || rec.MemberUsername != username || rec.NodeID != nodeID ||
		rec.ObservedState != "ACTIVE" || rec.DesiredState != "ACTIVE" || rec.MemberStorageDir == "" {
		return "", 0, 0, fmt.Errorf("workspace registry identity mismatch")
	}
	live, _, err := c.inspectRaw(ctx, rec.CanonicalContainerID)
	if err != nil {
		return "", 0, 0, err
	}
	if _, err := c.verifyContainerBinding(ctx, workspaceID, live); err != nil {
		return "", 0, 0, err
	}
	if !live.State.Running || live.Config.User != strconv.Itoa(rec.UID)+":"+strconv.Itoa(rec.GID) ||
		rec.UID < MinWorkspaceUID || rec.GID < MinWorkspaceUID {
		return "", 0, 0, fmt.Errorf("workspace runtime identity mismatch")
	}
	for _, mount := range live.Mounts {
		if mount.Type == "bind" && mount.RW && mount.Source == rec.MemberStorageDir &&
			mount.Destination == rec.ContainerMountPath {
			if err := ValidateSafeStorageRoot(mount.Source); err != nil {
				return "", 0, 0, err
			}
			return mount.Source, rec.UID, rec.GID, nil
		}
	}
	return "", 0, 0, fmt.Errorf("trusted workspace storage mount missing")
}
