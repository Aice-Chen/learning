# 个人学习系统（MCP 版）

AI 辅助学习系统的远程版本。学习状态保存在你的服务器上，claude.ai、ChatGPT、手机，以及本地的 Claude Code 和 Codex 都通过同一个 MCP 服务读写，在任何客户端上都能接着学。

- 给模型的协作说明：AGENTS.md
- 设计理由与架构：DESIGN.md
- 部署与客户端接入：DEPLOY.md
- 网页端 Project 指令：PROJECT_INSTRUCTIONS.md
- 服务端规格与代码：server/SPEC.md、server/learning_mcp/

## 开始使用

按 DEPLOY.md 完成部署和接入，并跑一遍其中的实测清单。之后：

- 网页端和手机：在学习用的 Project 里对话。
- 本地：在学习仓库目录启动 Claude Code 或 Codex。它们会自动加载 AGENTS.md，状态通过 learning 工具读写，材料和作业代码直接读本地文件。

## 日常使用

| 你说 | 模型做什么 |
|---|---|
| 今天学 cie6053 Lab2 | 调用 start_session 读取状态；有新材料时先登记进大纲，再开始学习 |
| 收尾 | 用 write_log 写一条学习记录，用 update_progress 更新进度 |
| 整合 | 综合近期记录更新概念掌握情况，列出变更等你确认后写入 |
| 加入资源，附链接或文件 | 用 fetch_materials 下载公开文件，建立该资源的进度 |
| 检查 cs336 的更新 | 重新读取资源主页，下载并登记新增的材料 |
| 看看我的作业（本地客户端） | 读代码和提交历史，分析其中反映出的理解程度 |
| 直接讲 / 考我 | 切换为直接讲解 / 集中检验 |

每次写入都会由服务端自动提交并推送到 GitHub，在本地 `git pull` 就能看到。每周的新课件放进本地的 sources/<资源名>/，Syncthing 会同步到服务器，下次学这个资源时模型会发现并登记。

## 放入材料前

sources/ 里只放模型可以直接读取的文件，转换在放入前完成：

| 材料 | 处理方式 |
|---|---|
| 文字为主的 PDF（论文、文字为主的书） | 用 markitdown 转成 md：`markitdown 文件.pdf -o 文件.md`。扫一眼公式和表格，有损坏就保留 PDF |
| 图表、公式为主的 PDF（课件、图多的教材） | 保留 PDF 原件 |
| 整本书 | 按章拆开，每章一个文件 |
| pptx、docx | 用 PowerPoint、Word 或 WPS 导出为 PDF，再按上面两条判断 |
| 网页、视频 | 用你自己的工具转成 md 或文稿 |

资源文件夹用英文小写名，如 `cie6053`、`cs336`；文件名对应资源结构，如 `lecture-05.pdf`、`ch08.md`。

## 调整系统

- 使用中遇到不顺手的地方，在 AGENTS.md 的"我的反馈"一节补一行，commit 并 push。服务端下次开场时会拉取。
- 需要改动系统本身时，在本地让 Claude Code 或 Codex 改。它会先读 DESIGN.md（涉及服务端的还会读 server/SPEC.md），说明要改什么、为什么，等你确认。服务端代码改动后，按 DEPLOY.md 第 14 步重启服务。

## 目录

```
learning-mcp/
├── README.md                给你看的概览
├── DEPLOY.md                部署与接入
├── PROJECT_INSTRUCTIONS.md  网页端 Project 指令
├── AGENTS.md                给模型的协作说明
├── CLAUDE.md                导入 AGENTS.md
├── DESIGN.md                设计理由与架构
├── standards/               格式规范
├── state/                   学习状态（只由服务端写入；learner.md 由你在本地修改后推送）
├── sources/                 学习材料（Syncthing 同步，不进 git）
├── notes/                   你的笔记（暂未纳入系统设计）
└── server/                  MCP 服务端
    ├── SPEC.md              工具规格与不变量
    ├── pyproject.toml
    ├── learning_mcp/        实现
    └── tests/               冒烟测试
```
