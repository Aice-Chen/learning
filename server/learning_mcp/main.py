"""MCP 服务端入口：注册工具、认证与启动。"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Annotated
from urllib.parse import unquote, urlparse

import httpx
from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from fastmcp.server.auth import AuthContext
from fastmcp.utilities.types import File
from pydantic import Field

from .config import Settings
from .gitsync import GitSync
from .store import TEXT_SUFFIXES, LearningStore, StoreError, human_size

SERVER_INSTRUCTIONS = (
    "这是学习者的个人学习系统。每次学习会话开始时调用 start_session，"
    "并按其中的 AGENTS.md 与学习者协作。"
)

READ_ONLY = {"readOnlyHint": True, "destructiveHint": False, "openWorldHint": False}
ADDITIVE = {"readOnlyHint": False, "destructiveHint": False, "openWorldHint": False}
OVERWRITE = {"readOnlyHint": False, "destructiveHint": True, "idempotentHint": True, "openWorldHint": False}
DOWNLOAD = {"readOnlyHint": False, "destructiveHint": False, "openWorldHint": True}

PDF_NOTE = (
    "本结果附带 PDF 文件 {name}（{desc}）。"
    "如果你看不到这份 PDF 的内容，说明当前客户端不支持读取工具返回的 PDF，"
    "请直接告诉学习者，由学习者更换客户端。"
)


def _section(title: str, body: str) -> str:
    return f"===== {title} =====\n{body.rstrip()}\n"


def _build_auth(settings: Settings):
    if not settings.auth_enabled:
        return None, None
    from fastmcp.server.auth.providers.github import GitHubProvider

    provider = GitHubProvider(
        client_id=settings.github_client_id,
        client_secret=settings.github_client_secret,
        base_url=settings.base_url,
        jwt_signing_key=settings.jwt_signing_key,
    )
    allowed = settings.allowed_github_user.lower()

    def owner_only(ctx: AuthContext) -> bool:
        token = ctx.token
        login = (token.claims.get("login") if token and token.claims else None) or ""
        return login.lower() == allowed

    return provider, owner_only


def create_server(settings: Settings) -> FastMCP:
    store = LearningStore(settings.repo)
    git = GitSync(settings.repo, settings.git_enabled, settings.git_name, settings.git_email)
    provider, owner_only = _build_auth(settings)
    mcp = FastMCP(name="learning", instructions=SERVER_INSTRUCTIONS, auth=provider)

    def tool(annotations: dict):
        kwargs = {"annotations": annotations}
        if owner_only:
            kwargs["auth"] = owner_only
        return mcp.tool(**kwargs)

    def fail(exc: Exception):
        raise ToolError(str(exc)) from exc

    # ======================= 读 =======================

    @tool(READ_ONLY)
    def start_session(
        resource: Annotated[str | None, Field(description="今天要学的资源名，如 cie6053。不传时只返回资源列表和整体状态。")] = None,
        include_guide: Annotated[bool, Field(description="是否返回 AGENTS.md。上下文里已经有 AGENTS.md 的客户端（在学习仓库中运行的 Claude Code、Codex）设为 false。")] = True,
    ) -> str:
        """每次学习会话开始时调用。返回协作说明 AGENTS.md（include_guide 为 true 时）、学习者自述 learner.md、概念掌握情况 concepts.md、上次整合之后的全部 log、指定资源的 progress 和材料变化，以及资源列表。"""
        try:
            note = git.refresh()
            parts: list[str] = []
            if note:
                parts.append(f"（同步提示：{note}）\n")
            if include_guide:
                parts.append(_section("AGENTS.md", store.read_agents()))
            parts.append(_section("state/learner.md", store.read_learner()))
            marker, concepts = store.read_concepts()
            parts.append(_section("state/concepts.md", concepts or "（空）"))
            logs = store.unintegrated_logs()
            head = f"上次整合（{marker}）之后的 log" if marker else "全部 log（尚未整合过）"
            parts.append(f"===== {head}：共 {len(logs)} 条 =====\n")
            for name in logs:
                parts.append(_section(f"state/log/{name}", store.read_log(name)))
            if resource:
                resource = store.check_resource(resource)
                progress = store.read_progress(resource)
                parts.append(_section(
                    f"state/progress/{resource}.md",
                    progress if progress is not None else "（这个资源还没有 progress，需要先加入资源）",
                ))
                new, updated = store.material_changes(resource)
                lines = []
                if new:
                    lines.append("未登记的新材料：" + "、".join(new))
                if updated:
                    lines.append("已更新的材料（修改时间晚于 progress）：" + "、".join(updated))
                parts.append(_section(f"sources/{resource} 的材料变化", "\n".join(lines) or "没有新材料。"))
            resources = store.list_resources()
            parts.append(_section("资源列表", "、".join(resources) or "（还没有资源）"))
            return "\n".join(parts)
        except StoreError as exc:
            fail(exc)

    @tool(READ_ONLY)
    def read_logs(
        names: Annotated[list[str], Field(description="log 文件名列表，如 [\"2026-10-08 1430 cie6053.md\"]")],
    ) -> str:
        """按文件名读取 log，用于依据 concepts.md 的"相关记录"回看更早的学习记录。"""
        try:
            return "\n".join(_section(f"state/log/{n}", store.read_log(n)) for n in names)
        except StoreError as exc:
            fail(exc)

    @tool(READ_ONLY)
    def search_logs(
        query: Annotated[str, Field(description="要搜索的文本，不区分大小写。查某个概念的学习历史时搜 [[概念名]]。")],
    ) -> str:
        """在全部 log 中搜索文本，返回命中的文件名、行号和所在行。"""
        try:
            hits = store.search_logs(query)
        except StoreError as exc:
            fail(exc)
        if not hits:
            return f"没有找到包含 {query!r} 的 log。"
        return "\n".join(f"{name}:{lineno}: {line}" for name, lineno, line in hits)

    @tool(READ_ONLY)
    def read_progress(
        resource: Annotated[str, Field(description="资源名")],
    ) -> str:
        """读取某个资源的 progress。当前资源的 progress 已包含在 start_session 的结果中，这个工具用于查看其他资源。"""
        try:
            text = store.read_progress(resource)
        except StoreError as exc:
            fail(exc)
        return text if text is not None else f"资源 {resource} 还没有 progress。"

    @tool(READ_ONLY)
    def read_standard(
        name: Annotated[str, Field(description="规范名：log-format、progress-format、concepts-format、materials")],
    ) -> str:
        """读取一份格式规范。写入对应文件前调用。"""
        try:
            return store.read_standard(name)
        except StoreError as exc:
            fail(exc)

    @tool(READ_ONLY)
    def list_sources(
        resource: Annotated[str, Field(description="资源名")],
    ) -> str:
        """列出某个资源的学习材料：文件大小、是否已在 progress 中登记；PDF 另附页数和自带的书签目录（书签可用来确定 read_source 的页码）。"""
        try:
            entries = store.iter_sources(resource)
            new, updated = store.material_changes(resource)
        except StoreError as exc:
            fail(exc)
        if not entries:
            return f"sources/{resource}/ 下还没有材料。"
        lines = [f"sources/{resource}/ 下的材料："]
        for e in entries:
            status = "未登记" if e.rel in new else ("已更新" if e.rel in updated else "已登记")
            if e.is_repo:
                lines.append(f"- {e.rel}  代码仓库，{e.file_count} 个文件  {status}")
                continue
            info = f"- {e.rel}  {human_size(e.size)}  {status}"
            if e.rel.lower().endswith(".pdf"):
                pages, outline, err = store.pdf_info(store.source_dir(resource) / e.rel)
                if pages:
                    info += f"  {pages} 页"
                if err:
                    info += f"  （{err}）"
                lines.append(info)
                lines.extend(f"    {row}" for row in outline)
            else:
                lines.append(info)
        return "\n".join(lines)

    @tool(READ_ONLY)
    def read_source(
        resource: Annotated[str, Field(description="资源名")],
        path: Annotated[str, Field(description="sources/<资源名>/ 下的相对路径，如 lecture-05.pdf 或 nano-vllm/README.md")],
        pages: Annotated[str | None, Field(description="只对 PDF 有效：要截取的页码，如 \"5-12,20\"。页码从 1 开始，指 PDF 的页序。不传时返回整份 PDF。")] = None,
    ):
        """读取一个学习材料。markdown 和文本文件返回文本；PDF 返回原件，可用 pages 截取。能直接访问本地文件的客户端，请直接读本地的 sources/。"""
        try:
            file_path = store.resolve_source(resource, path)
            if file_path.suffix.lower() == ".pdf":
                data, desc = store.pdf_bytes(file_path, pages)
                if len(data) > settings.max_pdf_bytes:
                    raise StoreError(
                        f"PDF 太大（{human_size(len(data))}）。请用 list_sources 查看书签，再用 pages 分段读取。"
                    )
                note = PDF_NOTE.format(name=file_path.name, desc=desc)
                return [note, File(data=data, format="pdf", name=file_path.name)]
            size = file_path.stat().st_size
            if file_path.suffix.lower() in TEXT_SUFFIXES or size < 1024 * 1024:
                if size > settings.max_text_bytes:
                    raise StoreError(f"文本文件太大（{human_size(size)}），请按章拆分后再放入。")
                try:
                    return file_path.read_text(encoding="utf-8")
                except UnicodeDecodeError:
                    pass
            raise StoreError("这个文件不是模型可直接读取的格式，只支持文本类文件和 PDF。")
        except StoreError as exc:
            fail(exc)
        except ValueError as exc:
            fail(StoreError(f"pages 参数格式不对：{exc}"))

    # ======================= 写 =======================

    @tool(ADDITIVE)
    def write_log(
        resource: Annotated[str, Field(description="资源名")],
        part: Annotated[str, Field(description="本次学习的部分，如 \"Lecture 5 外部中断；Lab2 前半\"")],
        body: Annotated[str, Field(description="按 log-format 规范写好的正文，从\"## 学了什么\"开始，不含 frontmatter")],
    ) -> str:
        """收尾时写一条学习记录。服务端按当前时间生成文件名和 frontmatter；已存在的 log 不会被覆盖。"""
        try:
            with git.transaction() as tx:
                path = store.write_log(resource, part, body)
                tx.commit([path], f"收尾：{path.stem}")
            return f"已写入 state/log/{path.name}。{tx.summary()}"
        except StoreError as exc:
            fail(exc)

    @tool(OVERWRITE)
    def update_progress(
        resource: Annotated[str, Field(description="资源名")],
        content: Annotated[str, Field(description="按 progress-format 规范写好的完整 progress，包括 frontmatter")],
    ) -> str:
        """建立或整份更新某个资源的 progress。在收尾、加入或更新资源时使用。"""
        try:
            with git.transaction() as tx:
                existed = store.read_progress(resource) is not None
                path = store.write_progress(resource, content)
                tx.commit([path], f"{'更新' if existed else '建立'}进度：{resource}")
            return f"已{'更新' if existed else '建立'} state/progress/{path.name}。{tx.summary()}"
        except StoreError as exc:
            fail(exc)

    @tool(OVERWRITE)
    def write_concepts(
        content: Annotated[str, Field(description="按 concepts-format 规范写好的完整内容，从第一个概念开始，不含 frontmatter")],
        integrated_through: Annotated[str, Field(description="本次整合覆盖到的最后一条 log 的文件名")],
    ) -> str:
        """整合时整份更新 concepts.md。服务端校验 integrated_through 指向的 log 存在、且不早于当前标记，并据此生成 frontmatter。"""
        try:
            with git.transaction() as tx:
                path = store.write_concepts(content, integrated_through)
                marker, _ = store.read_concepts()
                tx.commit([path], f"整合：至 {marker}")
            return f"已更新 state/concepts.md，整合标记推进到 {marker}。{tx.summary()}"
        except StoreError as exc:
            fail(exc)

    @tool(DOWNLOAD)
    def fetch_materials(
        resource: Annotated[str, Field(description="资源名")],
        urls: Annotated[list[str], Field(description="公开文件的下载链接列表")],
    ) -> str:
        """把公开链接的文件原样下载到服务器的 sources/<资源名>/，随后经 Syncthing 同步到学习者本地。不做格式转换；同名文件不覆盖。"""
        try:
            target = store.source_dir(resource)
        except StoreError as exc:
            fail(exc)
        target.mkdir(parents=True, exist_ok=True)
        report: list[str] = []
        with httpx.Client(follow_redirects=True, timeout=60) as client:
            for url in urls:
                parsed = urlparse(url)
                if parsed.scheme not in {"http", "https"}:
                    report.append(f"- 跳过 {url}：只支持 http 和 https 链接")
                    continue
                try:
                    with client.stream("GET", url) as resp:
                        resp.raise_for_status()
                        name = _filename(resp.headers.get("content-disposition"), parsed.path)
                        dest = target / name
                        if dest.exists():
                            report.append(f"- 跳过 {name}：同名文件已存在，未覆盖")
                            continue
                        size = 0
                        tmp = dest.with_name(dest.name + ".part")
                        with open(tmp, "wb") as f:
                            for chunk in resp.iter_bytes():
                                size += len(chunk)
                                if size > settings.max_download_bytes:
                                    raise StoreError("文件超过下载大小上限")
                                f.write(chunk)
                        tmp.rename(dest)
                        report.append(f"- 已保存 {name}（{human_size(size)}）")
                except (httpx.HTTPError, StoreError, OSError) as exc:
                    for leftover in target.glob("*.part"):
                        leftover.unlink(missing_ok=True)
                    report.append(f"- 下载失败 {url}：{exc}")
        return f"下载到 sources/{resource}/：\n" + "\n".join(report)

    return mcp


def _filename(disposition: str | None, url_path: str) -> str:
    name = ""
    if disposition:
        m = re.search(r"filename\*=UTF-8''([^;]+)", disposition) or re.search(r'filename="?([^";]+)"?', disposition)
        if m:
            name = unquote(m.group(1))
    if not name:
        name = unquote(Path(url_path).name)
    name = re.sub(r'[\\/:*?"<>|\x00-\x1f]', "_", name).strip(". ")
    return name or "download"


def main() -> None:
    settings = Settings.from_env()
    settings.validate()
    server = create_server(settings)
    if settings.transport == "stdio":
        server.run(transport="stdio")
    else:
        server.run(transport="http", host=settings.host, port=settings.port)


if __name__ == "__main__":
    main()
