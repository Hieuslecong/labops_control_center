from pathlib import Path
import subprocess

import pytest

WRAPPER = Path(__file__).resolve().parents[2] / "infrastructure/workspace-image/git-wrapper.sh"


@pytest.fixture
def wrapper(tmp_path):
    fake = tmp_path / "aiotgit"
    fake.write_text("#!/bin/bash\nprintf '%s\\n' \"$PWD\" \"$@\"\nexit 19\n")
    fake.chmod(0o755)
    test_wrapper = tmp_path / "git"
    test_wrapper.write_text(WRAPPER.read_text().replace("/usr/local/bin/aiotgit", str(fake)))
    test_wrapper.chmod(0o755)
    return test_wrapper


def invoke(wrapper, tmp_path, *args):
    return subprocess.run([str(wrapper), *args], cwd=tmp_path, text=True, capture_output=True, check=False)


@pytest.mark.parametrize("flags,args", [
    ([], ["https://github.com/owner/repo"]),
    (["--no-pager"], ["https://github.com/owner/repo", "destination"]),
    (["-C", "projects"], ["-b", "topic", "https://github.com/owner/repo"]),
    ([], ["--branch=topic", "https://github.com/owner/repo"]),
])
def test_wrapper_routes_clone_and_preserves_exit_code(wrapper, tmp_path, flags, args):
    (tmp_path / "projects").mkdir()
    result = invoke(wrapper, tmp_path, *flags, "clone", *args)
    directory = tmp_path / "projects" if "-C" in flags else tmp_path
    assert result.returncode == 19
    assert result.stdout.splitlines() == [str(directory), "clone", *args]
    assert not (tmp_path / "repo").exists()


@pytest.mark.parametrize("flags,args", [
    (["-c", "protocol.version=2"], []),
    ([], ["--depth", "1"]),
    ([], ["--filter=blob:none"]),
    ([], ["--recurse-submodules"]),
    ([], ["--upload-pack=custom"]),
    ([], ["--config", "core.editor=custom"]),
    ([], ["--no-deliver"]),
])
def test_wrapper_rejects_unsupported_clone_without_invoking_broker(wrapper, tmp_path, flags, args):
    result = invoke(wrapper, tmp_path, *flags, "clone", *args, "https://github.com/owner/repo")
    assert result.returncode == 1
    assert not result.stdout
    assert "does not support" in result.stderr


def test_wrapper_dispatches_non_clone_to_real_git(wrapper, tmp_path):
    version = invoke(wrapper, tmp_path, "--version")
    assert version.returncode == 0
    assert version.stdout.startswith("git version ")
    assert invoke(wrapper, tmp_path, "init", "-q").returncode == 0
    args = ["-C", str(tmp_path), "-c", "color.status=never", "status", "--short"]
    expected = subprocess.run(["/usr/bin/git", *args], text=True, capture_output=True, check=False)
    actual = invoke(wrapper, tmp_path, *args)
    assert (actual.returncode, actual.stdout, actual.stderr) == (expected.returncode, expected.stdout, expected.stderr)


def test_wrapper_preserves_nonclone_error_when_directory_is_missing(wrapper, tmp_path):
    args = ["-C", str(tmp_path / "missing"), "status"]
    expected = subprocess.run(["/usr/bin/git", *args], text=True, capture_output=True, check=False)
    actual = invoke(wrapper, tmp_path, *args)
    assert (actual.returncode, actual.stdout, actual.stderr) == (expected.returncode, expected.stdout, expected.stderr)
