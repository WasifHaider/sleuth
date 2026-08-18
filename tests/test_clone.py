import subprocess
from pathlib import Path

import pytest

from sleuth.ingest.clone import CloneError, clone_repo, list_source_files


@pytest.fixture
def local_git_repo(tmp_path):
    repo_dir = tmp_path / "source_repo"
    repo_dir.mkdir()
    subprocess.run(["git", "init"], cwd=repo_dir, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo_dir, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=repo_dir, check=True)

    (repo_dir / "main.py").write_text("def foo():\n    return 1\n")
    (repo_dir / "README.md").write_text("hello\n")

    subprocess.run(["git", "add", "."], cwd=repo_dir, check=True)
    subprocess.run(["git", "commit", "-m", "initial"], cwd=repo_dir, check=True, capture_output=True)
    return repo_dir


def test_clone_repo_copies_committed_files(local_git_repo, tmp_path):
    dest = tmp_path / "cloned"

    result = clone_repo(str(local_git_repo), str(dest))

    assert result == dest
    assert (dest / "main.py").exists()


def test_clone_repo_raises_on_invalid_source(tmp_path):
    dest = tmp_path / "cloned"

    with pytest.raises(CloneError):
        clone_repo(str(tmp_path / "does_not_exist"), str(dest))


def test_list_source_files_filters_by_extension(local_git_repo):
    files = list_source_files(local_git_repo, {".py"})

    names = {f.name for f in files}
    assert names == {"main.py"}
