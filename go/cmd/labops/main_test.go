package main

import (
	"bytes"
	"strings"
	"testing"
)

func TestLegacyRepositoryCommandsFailClosed(t *testing.T) {
	for _, command := range [][]string{{"clone", "https://github.com/example/repo.git"}, {"repo", "deliver", "req-123", "/tmp/repo"}} {
		var stdout, stderr bytes.Buffer
		if code := run(command, &stdout, &stderr); code == 0 {
			t.Fatalf("legacy repository command %q unexpectedly succeeded", command)
		}
		if !strings.Contains(stderr.String(), "aiotgit") {
			t.Fatalf("legacy command %q did not direct users to the approved CLI: %q", command, stderr.String())
		}
		if stdout.Len() != 0 {
			t.Fatalf("legacy repository command %q produced success output: %q", command, stdout.String())
		}
	}
}

func TestUsageKeepsLocalGitAndAIOTGITSeparate(t *testing.T) {
	var stdout, stderr bytes.Buffer
	if code := run([]string{"help"}, &stdout, &stderr); code != 0 {
		t.Fatalf("help returned %d", code)
	}
	for _, expected := range []string{"aiotgit clone", "aiotgit status", "Use git for local Git operations"} {
		if !strings.Contains(stdout.String(), expected) {
			t.Fatalf("usage is missing %q: %s", expected, stdout.String())
		}
	}
	if strings.Contains(stdout.String(), "labops clone") || strings.Contains(stdout.String(), "labops repo deliver") {
		t.Fatalf("usage advertises disabled repository operations: %s", stdout.String())
	}
}
