import os
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
UNIERP = ROOT / "demo" / "uni-erp"


class GitRepo:
    """A throwaway git repository for tests."""

    def __init__(self, path: Path):
        self.path = path
        path.mkdir(parents=True, exist_ok=True)
        self.run("init", "-q", "-b", "main")
        self.run("config", "user.email", "dev@example.com")
        self.run("config", "user.name", "Dev")
        self.run("config", "core.autocrlf", "false")

    def run(self, *args: str, env: dict | None = None) -> str:
        return subprocess.run(
            ["git", *args], cwd=self.path, check=True, capture_output=True, text=True, env=env
        ).stdout.strip()

    def write(self, rel: str, content: str) -> None:
        target = self.path / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8", newline="\n")

    def commit(self, message: str, files: dict[str, str] | None = None, author: str = "Dev <dev@example.com>", date: str | None = None) -> str:
        for rel, content in (files or {}).items():
            if content is None:
                (self.path / rel).unlink()
            else:
                self.write(rel, content)
        self.run("add", "-A")
        env = dict(os.environ)
        if date:
            env["GIT_AUTHOR_DATE"] = env["GIT_COMMITTER_DATE"] = date
        self.run("commit", "-q", "--allow-empty", "-m", message, "--author", author, env=env)
        return self.run("rev-parse", "HEAD")


@pytest.fixture
def git_repo(tmp_path):
    return GitRepo(tmp_path / "repo")


@pytest.fixture(scope="session")
def unierp_sources():
    return {
        p.relative_to(UNIERP).as_posix(): p.read_text(encoding="utf-8")
        for p in UNIERP.rglob("*.py")
        if "history" not in p.parts and "__pycache__" not in p.parts
    }
