package repositoryguard

import (
	"archive/tar"
	"compress/gzip"
	"crypto/sha256"
	"encoding/hex"
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestDeliveryRequiresTrustedWorkspaceRegistry(t *testing.T) {
	svc, err := NewGuardService(t.TempDir())
	if err != nil {
		t.Fatal(err)
	}
	dest := filepath.Join(t.TempDir(), "arbitrary")
	err = svc.Deliver("request", dest)
	if err == nil || !strings.Contains(err.Error(), "unavailable") {
		t.Fatalf("expected registry guard, got %v", err)
	}
}

type archiveEntry struct {
	name string
	kind byte
	data string
	link string
}

func makeTestArchive(t *testing.T, dir string, entries []archiveEntry) (string, string) {
	t.Helper()
	archive := filepath.Join(dir, "artifact.tar.gz")
	f, err := os.Create(archive)
	if err != nil {
		t.Fatal(err)
	}
	gz := gzip.NewWriter(f)
	tw := tar.NewWriter(gz)
	for _, entry := range entries {
		h := &tar.Header{Name: entry.name, Mode: 0640, Size: int64(len(entry.data)), Typeflag: entry.kind, Linkname: entry.link}
		if entry.kind == tar.TypeDir || entry.kind == tar.TypeSymlink || entry.kind == tar.TypeLink {
			h.Size = 0
		}
		if err := tw.WriteHeader(h); err != nil {
			t.Fatal(err)
		}
		if h.Size > 0 {
			if _, err := tw.Write([]byte(entry.data)); err != nil {
				t.Fatal(err)
			}
		}
	}
	if err := tw.Close(); err != nil {
		t.Fatal(err)
	}
	if err := gz.Close(); err != nil {
		t.Fatal(err)
	}
	if err := f.Close(); err != nil {
		t.Fatal(err)
	}
	contents, err := os.ReadFile(archive)
	if err != nil {
		t.Fatal(err)
	}
	digest := sha256.Sum256(contents)
	return archive, hex.EncodeToString(digest[:])
}

func TestDeliveryRejectsAdversarialArchivesWithoutPublishingPartialTree(t *testing.T) {
	tests := []struct {
		name    string
		entries []archiveEntry
	}{
		{name: "parent traversal", entries: []archiveEntry{{name: "safe.txt", kind: tar.TypeReg, data: "safe"}, {name: "../escape.txt", kind: tar.TypeReg, data: "escape"}}},
		{name: "absolute path", entries: []archiveEntry{{name: "/tmp/escape.txt", kind: tar.TypeReg, data: "escape"}}},
		{name: "symlink", entries: []archiveEntry{{name: "link", kind: tar.TypeSymlink, link: "../outside"}}},
		{name: "hardlink", entries: []archiveEntry{{name: "linked", kind: tar.TypeLink, link: "../outside"}}},
		{name: "duplicate path", entries: []archiveEntry{{name: "same.txt", kind: tar.TypeReg, data: "one"}, {name: "same.txt", kind: tar.TypeReg, data: "two"}}},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			root := t.TempDir()
			archive, digest := makeTestArchive(t, root, tt.entries)
			dest := filepath.Join(root, "workspace")
			if err := DeliverArtifact(archive, digest, dest); err == nil {
				t.Fatal("malicious archive accepted")
			}
			if _, err := os.Lstat(dest); !os.IsNotExist(err) {
				t.Fatalf("partial destination was published: %v", err)
			}
			entries, err := os.ReadDir(root)
			if err != nil {
				t.Fatal(err)
			}
			for _, entry := range entries {
				if strings.HasPrefix(entry.Name(), ".labops-delivery-") || strings.HasPrefix(entry.Name(), ".labops-artifact-") {
					t.Fatalf("staging artifact leaked: %s", entry.Name())
				}
			}
		})
	}
}

func TestDeliveryRejectsSymlinkParentExistingDestinationAndArtifactSymlink(t *testing.T) {
	root := t.TempDir()
	archive, digest := makeTestArchive(t, root, []archiveEntry{{name: "file.txt", kind: tar.TypeReg, data: "content"}})
	outside := filepath.Join(root, "outside")
	if err := os.Mkdir(outside, 0700); err != nil {
		t.Fatal(err)
	}
	linkParent := filepath.Join(root, "parent-link")
	if err := os.Symlink(outside, linkParent); err != nil {
		t.Fatal(err)
	}
	if err := DeliverArtifact(archive, digest, filepath.Join(linkParent, "workspace")); err == nil {
		t.Fatal("symlink destination parent accepted")
	}
	if err := os.Mkdir(filepath.Join(root, "exists"), 0700); err != nil {
		t.Fatal(err)
	}
	if err := DeliverArtifact(archive, digest, filepath.Join(root, "exists")); err == nil {
		t.Fatal("existing destination accepted")
	}
	artifactLink := filepath.Join(root, "artifact-link.tar.gz")
	if err := os.Symlink(archive, artifactLink); err != nil {
		t.Fatal(err)
	}
	if err := DeliverArtifact(artifactLink, digest, filepath.Join(root, "link-dest")); err == nil {
		t.Fatal("artifact symlink accepted")
	}
}

func TestDeliveryEnforcesEntryLimitAndDigest(t *testing.T) {
	root := t.TempDir()
	many := make([]archiveEntry, maxArchiveEntries+1)
	for i := range many {
		many[i] = archiveEntry{name: fmt.Sprintf("f-%05d", i), kind: tar.TypeReg}
	}
	archive, digest := makeTestArchive(t, root, many)
	if err := DeliverArtifact(archive, digest, filepath.Join(root, "many")); err == nil {
		t.Fatal("archive entry limit was not enforced")
	}
	valid, digest := makeTestArchive(t, root, []archiveEntry{{name: "file", kind: tar.TypeReg, data: "x"}})
	if err := DeliverArtifact(valid, strings.Repeat("0", 64), filepath.Join(root, "wrong-digest")); err == nil {
		t.Fatal("digest mismatch accepted")
	}
	if _, err := os.Lstat(filepath.Join(root, "wrong-digest")); !os.IsNotExist(err) {
		t.Fatalf("destination created on digest mismatch: %v", err)
	}
}
