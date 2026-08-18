import subprocess
from pathlib import Path


class CloneError(Exception):
    pass


def clone_repo(url: str, dest_dir: str) -> Path:
    dest = Path(dest_dir)
    result = subprocess.run(
        ["git", "clone", "--depth", "1", url, str(dest)],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise CloneError(result.stderr.strip())
    return dest


def list_source_files(repo_path: Path, extensions: set[str]) -> list[Path]:
    files = []
    for path in Path(repo_path).rglob("*"):
        if ".git" in path.parts:
            continue
        if path.is_file() and path.suffix in extensions:
            files.append(path)
    return files
