"""git 同步：写入串行化、写入前拉取、写入后提交并推送。

服务端是状态的唯一写入者。学习者在本地修改规则文件（AGENTS.md、standards/ 等）
或 learner.md 后推送到 GitHub，服务端在开场和每次写入前拉取。两边改的是不同的文件，
所以 rebase 不会冲突。
"""

from __future__ import annotations

import subprocess
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator


class GitSync:
    def __init__(self, root: Path, enabled: bool, name: str, email: str) -> None:
        self.root = root
        self.enabled = enabled and (root / ".git").exists()
        self.name = name
        self.email = email
        self.lock = threading.Lock()

    def _git(self, *args: str, timeout: int = 60) -> subprocess.CompletedProcess[str]:
        cmd = ["git", "-c", f"user.name={self.name}", "-c", f"user.email={self.email}", *args]
        return subprocess.run(
            cmd, cwd=self.root, capture_output=True, text=True, timeout=timeout
        )

    def _has_remote(self) -> bool:
        result = self._git("remote")
        return result.returncode == 0 and bool(result.stdout.strip())

    def _pull(self) -> str | None:
        """拉取远程更新。成功返回 None，失败返回说明文字。"""
        if not (self.enabled and self._has_remote()):
            return None
        try:
            result = self._git("pull", "--rebase", "--autostash")
        except subprocess.TimeoutExpired:
            return "从 GitHub 拉取超时，本次使用服务器上已有的版本。"
        if result.returncode != 0:
            self._git("rebase", "--abort")
            detail = (result.stderr or result.stdout).strip().splitlines()[-1:] or [""]
            return f"从 GitHub 拉取失败（{detail[0]}），本次使用服务器上已有的版本。"
        return None

    def refresh(self) -> str | None:
        """开场时调用：拉取学习者推送的规则和 learner.md 修改。"""
        with self.lock:
            return self._pull()

    @contextmanager
    def transaction(self) -> Iterator["Transaction"]:
        """一次写入操作：持锁、先拉取，再由调用方写文件并提交。"""
        with self.lock:
            tx = Transaction(self)
            note = self._pull()
            if note:
                tx.notes.append(note)
            yield tx


class Transaction:
    def __init__(self, sync: GitSync) -> None:
        self.sync = sync
        self.notes: list[str] = []

    def commit(self, paths: list[Path], message: str) -> None:
        sync = self.sync
        if not sync.enabled:
            return
        rel = [str(p.relative_to(sync.root)) for p in paths]
        add = sync._git("add", "--", *rel)
        if add.returncode != 0:
            self.notes.append(f"git add 失败：{add.stderr.strip()}")
            return
        commit = sync._git("commit", "-m", message, "--", *rel)
        if commit.returncode != 0:
            output = (commit.stdout + commit.stderr).strip()
            if "nothing to commit" in output or "no changes added" in output:
                self.notes.append("内容没有变化，未产生新的提交。")
            else:
                self.notes.append(f"git commit 失败：{output.splitlines()[-1] if output else ''}")
            return
        if not sync._has_remote():
            self.notes.append("已提交到服务器上的仓库（未配置远程仓库，没有推送）。")
            return
        try:
            push = sync._git("push", timeout=90)
        except subprocess.TimeoutExpired:
            self.notes.append("已提交到服务器上的仓库，推送到 GitHub 超时；下次写入时会一并推送。")
            return
        if push.returncode != 0:
            detail = push.stderr.strip().splitlines()[-1:] or [""]
            self.notes.append(
                f"已提交到服务器上的仓库，推送到 GitHub 失败（{detail[0]}）；下次写入时会一并推送。"
            )
        else:
            self.notes.append("已提交并推送到 GitHub。")

    def summary(self) -> str:
        return " ".join(self.notes)
