# learning MCP 服务端规格

本文件是服务端的依据：修改服务端代码时以它为准，改动工具行为时先改这里。
设计理由见仓库根目录的 DESIGN.md。

## 职责

服务端只负责存取、不变量、git 同步和新材料检测，不解析也不转换内容。内容格式由 standards/ 规定。

## 工具

读工具标注 readOnlyHint=true。写工具在服务端串行执行：先 `git pull --rebase`，再写文件，只提交刚写的文件，然后 push。

| 工具 | 参数 | 行为 |
|---|---|---|
| start_session | resource?, include_guide=true | 先 git pull。返回：AGENTS.md（include_guide 为 true 时）、state/learner.md、state/concepts.md、integrated_through 之后的全部 log（未整合过时为全部 log）、该资源的 progress、该资源的材料变化、资源列表 |
| read_logs | names[] | 按文件名返回 log；文件名可省略 .md |
| search_logs | query | 在全部 log 中不区分大小写地搜索，返回"文件名:行号: 行内容"，最多 200 条 |
| read_progress | resource | 返回该资源的 progress |
| read_standard | name | name 为 log-format、progress-format、concepts-format、materials 之一 |
| list_sources | resource | 列出材料的相对路径、大小、登记状态；PDF 附页数和书签目录（最多 80 条）；含 .git 的子文件夹作为一个代码仓库列出 |
| read_source | resource, path, pages? | 文本类文件返回文本（上限 2 MB）；PDF 返回一段说明文字加 PDF 原件（EmbeddedResource，application/pdf），pages 如 "5-12,20" 时只返回这些页（页码从 1 开始，指 PDF 页序），上限 30 MB；其他格式报错 |
| write_log | resource, part, body | 按服务器当前时间生成文件名 `YYYY-MM-DD HHMM <resource>.md` 和 frontmatter（date、source、part）；正文中若带 frontmatter 会被去掉；以排他模式创建，同名时依次改为 `... 2.md`、`... 3.md` |
| update_progress | resource, content | 整份写入 state/progress/<resource>.md，不存在则建立 |
| write_concepts | content, integrated_through | integrated_through 必须是已存在的 log 文件名，且不早于当前标记；服务端生成 frontmatter，content 中若带 frontmatter 会被去掉 |
| fetch_materials | resource, urls[] | 只接受 http/https；原样下载到 sources/<resource>/；文件名取自 Content-Disposition 或 URL；同名文件不覆盖；单个文件上限 200 MB；不进 git |

## 不变量

- 资源名只能包含小写字母、数字和连字符，以字母或数字开头。
- log 写入后不再修改；服务端没有修改或删除 log 的工具。
- log 文件名的字母顺序等于时间顺序，"整合标记之后的 log"按文件名比较得出。
- learner.md、AGENTS.md、standards/、DESIGN.md、server/ 没有写入工具，由学习者在本地修改后推送。
- read_source 的路径必须位于 sources/<资源名>/ 之内。

## 新材料检测

对 sources/<资源名>/ 下的每个文件（代码仓库以文件夹为单位），如果它的相对路径或文件名没有出现在该资源的 progress 文本中，报告为"未登记"；已出现、但修改时间晚于 progress 文件的，报告为"已更新"。忽略以点开头的文件和文件夹，以及 Syncthing 的临时文件。

## 认证

设置了 LEARNING_GITHUB_CLIENT_ID 时启用 GitHub 登录（FastMCP 的 GitHubProvider，即 OAuth 代理，负责动态客户端注册）。每个工具都附带一个检查：令牌中的 GitHub 用户名必须等于 LEARNING_ALLOWED_GITHUB_USER（不区分大小写）。未启用认证时，HTTP 服务只允许监听本机地址。

GitHub OAuth 只申请 `read:user`，用于读取登录身份。仓库的拉取与推送继续使用仓库部署密钥。

## 配置（环境变量）

| 变量 | 说明 | 默认值 |
|---|---|---|
| LEARNING_REPO | 学习仓库的路径 | server/ 的上一级目录 |
| LEARNING_TRANSPORT | http 或 stdio | http |
| LEARNING_HOST / LEARNING_PORT | 监听地址和端口 | 127.0.0.1 / 8000 |
| LEARNING_BASE_URL | 对外地址，如 https://learn.example.com | 无 |
| LEARNING_GITHUB_CLIENT_ID / _SECRET | GitHub OAuth App 的凭据 | 无 |
| LEARNING_JWT_SIGNING_KEY | 签发令牌用的密钥，保持不变，重启后已登录的客户端才不需要重新授权 | 无 |
| LEARNING_ALLOWED_GITHUB_USER | 唯一放行的 GitHub 用户名 | 无 |
| LEARNING_GIT | 是否启用 git 同步 | true |
| LEARNING_GIT_NAME / _EMAIL | 服务端提交时使用的身份 | learning-mcp |
| TZ | 时区，决定 log 的日期和时间 | 系统时区 |

## 测试

`cd server && uv run --locked --extra test python -m pytest -q`。测试在临时仓库上走一遍全部工具，包括 git 提交与推送、PDF 截取、整合标记校验和下载，不包含认证。
