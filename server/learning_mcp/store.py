"""学习仓库的文件读写与不变量。

这里只处理存取和可以确定的规则：路径、命名、log 不可覆盖、整合标记校验、
新材料检测。内容本身的格式由 standards/ 规定，服务端不解析。
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from datetime import datetime
from io import BytesIO
from pathlib import Path

from pypdf import PdfReader, PdfWriter

RESOURCE_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")
STANDARD_NAMES = ("log-format", "progress-format", "concepts-format", "materials")
TEXT_SUFFIXES = {
    ".md", ".markdown", ".txt", ".srt", ".vtt", ".csv", ".json", ".yaml", ".yml",
    ".py", ".c", ".h", ".cc", ".cpp", ".hpp", ".cu", ".cuh", ".rs", ".go", ".js",
    ".ts", ".sh", ".toml", ".ini", ".cfg", ".tex", ".rst", ".html", ".css", ".sql",
}
SKIP_NAMES = {".stfolder", ".stversions", ".stignore", ".DS_Store", "Thumbs.db", ".gitkeep"}


class StoreError(Exception):
    """可以直接展示给模型的错误。"""


@dataclass
class SourceEntry:
    rel: str            # sources/<资源名>/ 下的相对路径；代码仓库以 / 结尾
    size: int
    mtime: float
    is_repo: bool = False
    file_count: int = 0


def split_frontmatter(text: str) -> tuple[dict[str, str], str]:
    """拆出简单的 frontmatter（key: value 形式），返回 (字段, 正文)。"""
    if not text.startswith("---"):
        return {}, text
    lines = text.splitlines(keepends=True)
    if not lines or lines[0].strip() != "---":
        return {}, text
    fields: dict[str, str] = {}
    for i in range(1, len(lines)):
        line = lines[i]
        if line.strip() == "---":
            return fields, "".join(lines[i + 1:]).lstrip("\n")
        if ":" in line:
            key, _, value = line.partition(":")
            fields[key.strip()] = value.strip().strip('"')
    return {}, text


def human_size(size: int) -> str:
    if size < 1024:
        return f"{size} B"
    if size < 1024 * 1024:
        return f"{size / 1024:.0f} KB"
    return f"{size / 1024 / 1024:.1f} MB"


def parse_pages(spec: str, total: int) -> list[int]:
    """把 "5-12,20" 解析成从 0 开始的页序列表。页码从 1 开始。"""
    pages: list[int] = []
    for part in spec.replace("，", ",").split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            a, _, b = part.partition("-")
            start, end = int(a), int(b)
        else:
            start = end = int(part)
        if start < 1 or end > total or start > end:
            raise StoreError(f"页码范围 {part} 无效，这份 PDF 共 {total} 页。")
        pages.extend(range(start - 1, end))
    if not pages:
        raise StoreError("pages 参数为空。")
    return pages


class LearningStore:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.state = root / "state"
        self.log_dir = self.state / "log"
        self.progress_dir = self.state / "progress"
        self.concepts_path = self.state / "concepts.md"
        self.learner_path = self.state / "learner.md"
        self.sources = root / "sources"
        self.standards = root / "standards"
        self.agents_path = root / "AGENTS.md"

    # ---------- 基础 ----------

    @staticmethod
    def check_resource(name: str) -> str:
        name = (name or "").strip()
        if not RESOURCE_RE.match(name):
            raise StoreError(
                f"资源名 {name!r} 不符合规则：只能用小写字母、数字和连字符，例如 cie6053、cs336。"
            )
        return name

    @staticmethod
    def _read(path: Path) -> str:
        return path.read_text(encoding="utf-8") if path.exists() else ""

    def read_agents(self) -> str:
        return self._read(self.agents_path)

    def read_learner(self) -> str:
        return self._read(self.learner_path)

    def read_standard(self, name: str) -> str:
        key = name.strip().removesuffix(".md")
        if key not in STANDARD_NAMES:
            raise StoreError("可读取的规范有：" + "、".join(STANDARD_NAMES))
        return self._read(self.standards / f"{key}.md")

    def list_resources(self) -> list[str]:
        names = {p.stem for p in self.progress_dir.glob("*.md")}
        if self.sources.exists():
            names |= {
                p.name for p in self.sources.iterdir()
                if p.is_dir() and not p.name.startswith(".") and RESOURCE_RE.match(p.name)
            }
        return sorted(names)

    # ---------- log ----------

    def list_logs(self) -> list[str]:
        if not self.log_dir.exists():
            return []
        return sorted(p.name for p in self.log_dir.glob("*.md"))

    def read_concepts(self) -> tuple[str | None, str]:
        """返回 (integrated_through, 完整文本)。"""
        text = self._read(self.concepts_path)
        fields, _ = split_frontmatter(text)
        marker = fields.get("integrated_through") or None
        return marker, text

    def unintegrated_logs(self) -> list[str]:
        marker, _ = self.read_concepts()
        logs = self.list_logs()
        if not marker:
            return logs
        return [name for name in logs if name > marker]

    def read_log(self, name: str) -> str:
        name = name.strip()
        if not name.endswith(".md"):
            name += ".md"
        if "/" in name or "\\" in name or name not in self.list_logs():
            raise StoreError(f"没有名为 {name} 的 log。")
        return self._read(self.log_dir / name)

    def search_logs(self, query: str, max_hits: int = 200) -> list[tuple[str, int, str]]:
        needle = query.strip().lower()
        if not needle:
            raise StoreError("搜索内容不能为空。")
        hits: list[tuple[str, int, str]] = []
        for name in self.list_logs():
            for lineno, line in enumerate(self._read(self.log_dir / name).splitlines(), 1):
                if needle in line.lower():
                    hits.append((name, lineno, line.strip()))
                    if len(hits) >= max_hits:
                        return hits
        return hits

    def write_log(self, resource: str, part: str, body: str, now: datetime | None = None) -> Path:
        resource = self.check_resource(resource)
        _, body = split_frontmatter(body.strip())
        body = body.strip()
        if not body:
            raise StoreError("log 正文不能为空。")
        now = now or datetime.now().astimezone()
        stem = f"{now:%Y-%m-%d %H%M} {resource}"
        self.log_dir.mkdir(parents=True, exist_ok=True)
        text = (
            "---\n"
            f"date: {now:%Y-%m-%d}\n"
            f"source: {resource}\n"
            f"part: {json.dumps(part.strip(), ensure_ascii=False)}\n"
            "---\n\n"
            f"{body}\n"
        )
        for n in range(1, 100):
            name = f"{stem}.md" if n == 1 else f"{stem} {n}.md"
            path = self.log_dir / name
            try:
                with open(path, "x", encoding="utf-8") as f:   # 排他创建：已存在就换名字
                    f.write(text)
                return path
            except FileExistsError:
                continue
        raise StoreError("同一分钟内的 log 过多，无法生成文件名。")

    # ---------- progress ----------

    def progress_path(self, resource: str) -> Path:
        return self.progress_dir / f"{self.check_resource(resource)}.md"

    def read_progress(self, resource: str) -> str | None:
        path = self.progress_path(resource)
        return self._read(path) if path.exists() else None

    def write_progress(self, resource: str, content: str) -> Path:
        content = content.strip()
        if not content:
            raise StoreError("progress 内容不能为空。")
        path = self.progress_path(resource)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content + "\n", encoding="utf-8")
        return path

    # ---------- concepts ----------

    def write_concepts(self, content: str, integrated_through: str) -> Path:
        marker = integrated_through.strip()
        if marker and not marker.endswith(".md"):
            marker += ".md"
        logs = self.list_logs()
        if marker not in logs:
            raise StoreError(f"integrated_through 指向的 log {marker!r} 不存在。")
        current, _ = self.read_concepts()
        if current and marker < current:
            raise StoreError(f"integrated_through 不能早于当前的标记 {current}。")
        _, body = split_frontmatter(content.strip())
        text = f"---\nintegrated_through: {marker}\n---\n\n{body.strip()}\n"
        self.concepts_path.write_text(text, encoding="utf-8")
        return self.concepts_path

    # ---------- sources ----------

    def source_dir(self, resource: str) -> Path:
        return self.sources / self.check_resource(resource)

    def iter_sources(self, resource: str) -> list[SourceEntry]:
        """列出材料。含 .git 的子文件夹视为一个代码仓库整体。"""
        base = self.source_dir(resource)
        if not base.exists():
            return []
        entries: list[SourceEntry] = []
        for dirpath, dirnames, filenames in os.walk(base):
            current = Path(dirpath)
            if current != base and (current / ".git").exists():
                count = sum(len(f) for _, _, f in os.walk(current))
                entries.append(SourceEntry(
                    rel=str(current.relative_to(base)).replace(os.sep, "/") + "/",
                    size=0, mtime=current.stat().st_mtime, is_repo=True, file_count=count,
                ))
                dirnames[:] = []
                continue
            dirnames[:] = sorted(d for d in dirnames if not d.startswith(".") and d not in SKIP_NAMES)
            for fname in sorted(filenames):
                if fname.startswith(".") or fname in SKIP_NAMES or fname.startswith("~syncthing~"):
                    continue
                path = current / fname
                st = path.stat()
                entries.append(SourceEntry(
                    rel=str(path.relative_to(base)).replace(os.sep, "/"),
                    size=st.st_size, mtime=st.st_mtime,
                ))
        return entries

    def material_changes(self, resource: str) -> tuple[list[str], list[str]]:
        """返回 (未登记的新材料, 已登记但修改时间晚于 progress 的材料)。"""
        path = self.progress_path(resource)
        text = self._read(path)
        progress_mtime = path.stat().st_mtime if path.exists() else 0.0
        new, updated = [], []
        for entry in self.iter_sources(resource):
            key = entry.rel.rstrip("/")
            registered = key in text or Path(key).name in text
            if not registered:
                new.append(entry.rel)
            elif not entry.is_repo and entry.mtime > progress_mtime:
                updated.append(entry.rel)
        return new, updated

    def resolve_source(self, resource: str, rel: str) -> Path:
        base = self.source_dir(resource).resolve()
        path = (base / rel).resolve()
        if base != path and base not in path.parents:
            raise StoreError("路径超出了该资源的材料文件夹。")
        if not path.is_file():
            raise StoreError(f"找不到材料 {rel}。可以先用 list_sources 查看这个资源有哪些材料。")
        return path

    @staticmethod
    def pdf_info(path: Path, max_outline: int = 80) -> tuple[int | None, list[str], str | None]:
        """返回 (页数, 书签目录行, 错误说明)。"""
        try:
            reader = PdfReader(str(path))
            if reader.is_encrypted:
                return None, [], "PDF 已加密，无法读取页数和书签"
            total = len(reader.pages)
            lines: list[str] = []

            def walk(items, depth: int) -> None:
                for item in items:
                    if len(lines) >= max_outline:
                        return
                    if isinstance(item, list):
                        walk(item, depth + 1)
                        continue
                    try:
                        page = reader.get_destination_page_number(item) + 1
                        lines.append(f"{'  ' * depth}{item.title} → 第 {page} 页")
                    except Exception:
                        lines.append(f"{'  ' * depth}{getattr(item, 'title', '?')}")

            walk(reader.outline, 0)
            return total, lines, None
        except Exception as exc:  # 损坏或不规范的 PDF
            return None, [], f"无法解析 PDF：{exc.__class__.__name__}"

    @staticmethod
    def pdf_bytes(path: Path, pages: str | None) -> tuple[bytes, str]:
        """返回 (PDF 数据, 描述)。pages 为空时返回原件。"""
        if not pages:
            reader = PdfReader(str(path))
            return path.read_bytes(), f"共 {len(reader.pages)} 页"
        reader = PdfReader(str(path))
        indices = parse_pages(pages, len(reader.pages))
        writer = PdfWriter()
        for i in indices:
            writer.add_page(reader.pages[i])
        buf = BytesIO()
        writer.write(buf)
        return buf.getvalue(), f"截取第 {pages} 页，原文件共 {len(reader.pages)} 页"
