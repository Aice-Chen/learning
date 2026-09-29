"""冒烟测试：在临时仓库上走一遍全部工具（不含认证）。

运行：cd server && uv run --extra test python -m pytest -q
"""

from __future__ import annotations

import asyncio
import os
import shutil
import subprocess
import threading
import time
from datetime import datetime
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
from fastmcp import Client
from pypdf import PdfWriter

from learning_mcp.config import Settings
from learning_mcp.main import create_server
from learning_mcp.store import LearningStore, StoreError

REPO_TEMPLATE = Path(__file__).resolve().parents[2]


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout


def _make_pdf(path: Path, pages: int = 6) -> None:
    writer = PdfWriter()
    for _ in range(pages):
        writer.add_blank_page(width=200, height=200)
    ch1 = writer.add_outline_item("第 1 章 引言", 0)
    writer.add_outline_item("1.1 背景", 1, parent=ch1)
    writer.add_outline_item("第 2 章 方法", 3)
    with open(path, "wb") as f:
        writer.write(f)


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    work = tmp_path / "learning"
    shutil.copytree(REPO_TEMPLATE, work, ignore=shutil.ignore_patterns("server", ".git", ".venv"))
    remote = tmp_path / "remote.git"
    _git(tmp_path, "init", "--bare", "-b", "main", str(remote))
    _git(work, "init", "-b", "main")
    _git(work, "-c", "user.name=t", "-c", "user.email=t@t", "add", ".")
    _git(work, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-m", "init")
    _git(work, "remote", "add", "origin", str(remote))
    _git(work, "push", "-u", "origin", "main")
    (work / "sources" / "cie6053").mkdir(parents=True)
    _make_pdf(work / "sources" / "cie6053" / "lecture-05.pdf")
    (work / "sources" / "cie6053" / "notes.md").write_text("# 讲义\n中断", encoding="utf-8")
    return work


def _text(result) -> str:
    return "\n".join(getattr(c, "text", "") for c in result.content)


def run(coro):
    return asyncio.run(coro)


def test_full_flow(repo: Path):
    server = create_server(Settings(repo=repo))

    async def flow():
        async with Client(server) as c:
            tools = {t.name: t for t in await c.list_tools()}
            assert set(tools) == {
                "start_session", "read_logs", "search_logs", "read_progress", "read_standard",
                "list_sources", "read_source", "write_log", "update_progress",
                "write_concepts", "fetch_materials",
            }
            assert tools["start_session"].annotations.read_only_hint is True
            assert tools["write_log"].annotations.read_only_hint is False

            out = _text(await c.call_tool("start_session", {"resource": "cie6053"}))
            assert "===== AGENTS.md =====" in out
            assert "未登记的新材料：lecture-05.pdf、notes.md" in out

            out = _text(await c.call_tool("start_session", {"resource": "cie6053", "include_guide": False}))
            assert "===== AGENTS.md =====" not in out

            progress = (
                "---\nresource: cie6053\n---\n# CIE6053\n\n## 大纲\n"
                "| 部分 | 材料 | 状态 | 备注 |\n|---|---|---|---|\n"
                "| Lecture 5 | lecture-05.pdf、notes.md | 进行中 | |\n"
            )
            out = _text(await c.call_tool("update_progress", {"resource": "cie6053", "content": progress}))
            assert "已建立" in out and "已提交并推送" in out

            out = _text(await c.call_tool("start_session", {"resource": "cie6053", "include_guide": False}))
            assert "没有新材料" in out

            listing = _text(await c.call_tool("list_sources", {"resource": "cie6053"}))
            assert "6 页" in listing and "第 2 章 方法 → 第 4 页" in listing and "已登记" in listing

            res = await c.call_tool("read_source", {"resource": "cie6053", "path": "lecture-05.pdf", "pages": "2-3"})
            kinds = [type(x).__name__ for x in res.content]
            assert kinds == ["TextContent", "EmbeddedResource"], kinds
            assert "截取第 2-3 页" in res.content[0].text
            assert res.content[1].resource.mime_type == "application/pdf"

            md = _text(await c.call_tool("read_source", {"resource": "cie6053", "path": "notes.md"}))
            assert "中断" in md

            bad = await c.call_tool("read_source", {"resource": "cie6053", "path": "../../AGENTS.md"}, raise_on_error=False)
            assert bad.is_error

            body = "## 学了什么\n[[NVIC]] 与中断\n\n## 观察\n- [[NVIC]]：学习者主动提问。"
            w1 = _text(await c.call_tool("write_log", {"resource": "cie6053", "part": "Lecture 5: 中断", "body": body}))
            w2 = _text(await c.call_tool("write_log", {"resource": "cie6053", "part": "Lecture 5", "body": body}))
            name1 = w1.split("state/log/")[1].split("。")[0]
            name2 = w2.split("state/log/")[1].split("。")[0]
            assert name1 != name2

            hits = _text(await c.call_tool("search_logs", {"query": "[[nvic]]"}))
            assert name1 in hits

            bad = await c.call_tool("write_concepts", {"content": "### NVIC", "integrated_through": "2099-01-01 0000 x.md"}, raise_on_error=False)
            assert bad.is_error

            last = sorted([name1, name2])[-1]
            out = _text(await c.call_tool("write_concepts", {"content": "### NVIC\n- 掌握情况：待观察。", "integrated_through": last}))
            assert "整合标记推进到" in out

            out = _text(await c.call_tool("start_session", {"include_guide": False}))
            assert "共 0 条" in out

            first = sorted([name1, name2])[0]
            bad = await c.call_tool("write_concepts", {"content": "### NVIC", "integrated_through": first}, raise_on_error=False)
            assert bad.is_error

            std = _text(await c.call_tool("read_standard", {"name": "log-format"}))
            assert "log 格式" in std

    run(flow())

    remote_log = _git(repo, "log", "--oneline", "origin/main")
    assert "收尾" in remote_log and "整合" in remote_log and "进度" in remote_log
    concepts = (repo / "state" / "concepts.md").read_text(encoding="utf-8")
    assert concepts.startswith("---\nintegrated_through: ")
    log_text = next((repo / "state" / "log").glob("*.md")).read_text(encoding="utf-8")
    assert 'part: "Lecture 5: 中断"' in log_text or 'part: "Lecture 5"' in log_text


def test_log_names_sort_by_time(tmp_path: Path):
    store = LearningStore(tmp_path)
    a = store.write_log("zeta", "p", "## 学了什么\nx", now=datetime(2026, 10, 8, 9, 5))
    b = store.write_log("alpha", "p", "## 学了什么\nx", now=datetime(2026, 10, 8, 14, 30))
    assert store.list_logs() == [a.name, b.name]   # 同一天：按时间而不是按资源名排序


def test_rejects_bad_resource(tmp_path: Path):
    store = LearningStore(tmp_path)
    with pytest.raises(StoreError):
        store.write_log("../etc", "p", "x")


def test_fetch_materials(repo: Path, tmp_path: Path):
    site = tmp_path / "site"
    site.mkdir()
    (site / "slides.pdf").write_bytes(b"%PDF-1.4 test")
    handler = partial(SimpleHTTPRequestHandler, directory=str(site))
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{httpd.server_address[1]}/slides.pdf"
    server = create_server(Settings(repo=repo, git_enabled=False))

    async def flow():
        async with Client(server) as c:
            out1 = _text(await c.call_tool("fetch_materials", {"resource": "cs336", "urls": [url]}))
            out2 = _text(await c.call_tool("fetch_materials", {"resource": "cs336", "urls": [url]}))
            return out1, out2

    try:
        out1, out2 = run(flow())
    finally:
        httpd.shutdown()
    assert "已保存 slides.pdf" in out1
    assert "同名文件已存在" in out2
    assert (repo / "sources" / "cs336" / "slides.pdf").exists()
