import subprocess
from pathlib import Path

import pytest


class Repo:
    def __init__(self, root: Path):
        self.root = root
        self.git("init", "-q", "-b", "main")
        self.git("config", "user.email", "t@example.com")
        self.git("config", "user.name", "t")

    def git(self, *args: str) -> str:
        return subprocess.run(["git", "-C", str(self.root), *args], check=True, capture_output=True, text=True).stdout

    def write(self, path: str, text: str) -> None:
        p = self.root / path
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)

    def commit(self, msg: str = "c") -> None:
        self.git("add", "-A")
        self.git("commit", "-q", "-m", msg)


@pytest.fixture
def repo(tmp_path: Path) -> Repo:
    return Repo(tmp_path)
