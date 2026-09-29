# 部署与接入

按顺序完成下面各步。文中 `learn.example.com` 代指你的域名，`<你>` 代指你的 GitHub 用户名，`<VPS IP>` 代指服务器 IP。

## 0. 准备

- 一台东京的 VPS，系统 Ubuntu 24.04，1～2 核、2 GB 内存就够。
- 一个域名。
- GitHub 账号。

## 1. 把系统推到私有 GitHub 仓库（笔记本上）

先补完 state/learner.md 的"学习目标"一节，然后：

```bash
cd learning-mcp
git init -b main
git add .
git commit -m "初始化学习系统"
```

在 GitHub 上新建一个私有仓库 `learning`（不要勾选初始化 README），然后：

```bash
git remote add origin git@github.com:<你>/learning.git
git push -u origin main
```

## 2. 服务器基础环境

```bash
sudo apt update && sudo apt upgrade -y
sudo apt install -y git curl syncthing debian-keyring debian-archive-keyring apt-transport-https
sudo adduser --disabled-password --gecos "" learning

sudo ufw allow OpenSSH
sudo ufw allow 80,443/tcp
sudo ufw allow 22000
sudo ufw enable
```

VPS 服务商的安全组里也要放行 22、80、443 端口和 22000（TCP 与 UDP）。

## 3. 域名解析

添加一条 A 记录：`learn` → `<VPS IP>`。用 `ping learn.example.com` 确认解析生效。

## 4. 克隆仓库

以下命令用 learning 用户执行：

```bash
sudo -iu learning
ssh-keygen -t ed25519 -f ~/.ssh/github_deploy -N ""
cat ~/.ssh/github_deploy.pub
```

把输出的公钥添加到 GitHub 仓库的 Settings → Deploy keys，勾选 Allow write access。这把钥匙只能访问这一个仓库。然后：

```bash
cat >> ~/.ssh/config << 'CFG'
Host github.com
  IdentityFile ~/.ssh/github_deploy
  IdentitiesOnly yes
CFG
git clone git@github.com:<你>/learning.git ~/learning
```

## 5. 安装依赖并运行测试

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
source ~/.local/bin/env
cd ~/learning/server
uv sync --extra test
uv run --extra test python -m pytest -q
```

测试全部通过，说明工具逻辑、git 提交与推送、PDF 截取在这台服务器上都正常。

## 6. 创建 GitHub OAuth App

GitHub → Settings → Developer settings → OAuth Apps → New OAuth App：

- Application name：learning-mcp
- Homepage URL：`https://learn.example.com`
- Authorization callback URL：`https://learn.example.com/auth/callback`

创建后记下 Client ID，再点 Generate a new client secret 生成密钥。

## 7. 环境变量文件

仍以 learning 用户执行。先生成一个签名密钥：

```bash
openssl rand -hex 32
```

然后创建 `~/learning.env`，填入：

```bash
LEARNING_REPO=/home/learning/learning
LEARNING_TRANSPORT=http
LEARNING_HOST=127.0.0.1
LEARNING_PORT=8000
LEARNING_BASE_URL=https://learn.example.com
LEARNING_GITHUB_CLIENT_ID=<Client ID>
LEARNING_GITHUB_CLIENT_SECRET=<Client secret>
LEARNING_JWT_SIGNING_KEY=<上面生成的密钥>
LEARNING_ALLOWED_GITHUB_USER=<你>
LEARNING_GIT_NAME=learning-mcp
LEARNING_GIT_EMAIL=learning-mcp@users.noreply.github.com
TZ=Asia/Shanghai
```

```bash
chmod 600 ~/learning.env
exit
```

TZ 决定 log 的日期和时间，设成你所在的时区。签名密钥以后不要更换，否则所有客户端都要重新授权。

## 8. 用 systemd 运行服务

```bash
sudo tee /etc/systemd/system/learning-mcp.service << 'UNIT'
[Unit]
Description=Learning MCP server
After=network-online.target
Wants=network-online.target

[Service]
User=learning
WorkingDirectory=/home/learning/learning/server
EnvironmentFile=/home/learning/learning.env
ExecStart=/home/learning/.local/bin/uv run learning-mcp
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
UNIT
sudo systemctl daemon-reload
sudo systemctl enable --now learning-mcp
sudo systemctl status learning-mcp
```

## 9. Caddy 提供 HTTPS

```bash
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' | sudo gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' | sudo tee /etc/apt/sources.list.d/caddy-stable.list
sudo apt update && sudo apt install -y caddy
```

把 `/etc/caddy/Caddyfile` 的内容改为：

```
learn.example.com {
    reverse_proxy 127.0.0.1:8000
}
```

```bash
sudo systemctl reload caddy
```

验证：

```bash
curl -s -o /dev/null -w "%{http_code}\n" -X POST https://learn.example.com/mcp
curl -s https://learn.example.com/.well-known/oauth-authorization-server
```

第一条应当输出 401（未登录被拒绝），第二条应当返回一段包含 authorization_endpoint 的 JSON。

443 端口由 Caddy 使用。以后在同一台服务器上部署代理时，需要给代理分配其他端口，或者用 SNI 分流让两者共用 443。

## 10. Syncthing 同步材料

服务器上：

```bash
sudo systemctl enable --now syncthing@learning
```

服务器的 Syncthing 管理界面只监听本机。在笔记本上建一条 SSH 隧道来访问它；笔记本自己的 Syncthing 也占用 8384 端口，所以本地换成 18384：

```bash
ssh -L 18384:127.0.0.1:8384 <你的 SSH 用户>@<VPS IP>
```

浏览器打开 `http://127.0.0.1:18384` 就是服务器的界面。笔记本上安装 Syncthing（Windows 可用官方发行版或 SyncTrayzor）后，按下面配对：

1. 两端各自在"操作 → 显示 ID"里拿到设备 ID，互相添加为远程设备。笔记本添加服务器时，地址填 `tcp://<VPS IP>:22000`。
2. 在笔记本上添加共享文件夹：路径为本地学习仓库里的 `sources` 文件夹，共享给服务器。
3. 在服务器上接受共享，路径设为 `/home/learning/learning/sources`。
4. 两端都在该文件夹的设置里开启"文件版本控制"，选"简易版本控制"。一边误删文件时，另一边还能找回。

深圳到东京的直连如果不稳定，可以在以后部署代理后，让 Syncthing 的连接走代理（Syncthing 支持通过 all_proxy 环境变量使用 SOCKS5 代理）。

## 11. 本地仓库

笔记本上的学习仓库（第 1 步的那个）从此是只读镜像：state/ 由服务端写入，要看最新状态时 `git pull`。
修改 AGENTS.md、standards/、DESIGN.md 或 learner.md 时，在本地改完后 commit 并 push，服务端会在下一次 start_session 或写入时拉取。

## 12. 接入客户端

| 客户端 | 做法 |
|---|---|
| claude.ai 网页端、桌面端、手机 App | 设置 → 连接器 → 添加自定义连接器，名称 learning，URL `https://learn.example.com/mcp`，按提示用 GitHub 授权。桌面端和手机 App 使用同一账号的连接器 |
| ChatGPT | 在设置中开启开发者模式，创建连接器，URL 同上，认证方式选 OAuth。菜单名称以当前界面为准 |
| Claude Code | 在本地学习仓库目录运行 `claude mcp add --transport http learning https://learn.example.com/mcp --scope user`，再在 Claude Code 里运行 `/mcp` 完成授权 |
| Codex | 按当前版本的文档添加远程 MCP 服务器（URL 同上）并完成 OAuth 登录 |

网页端需要一个 Project：在 claude.ai 和 ChatGPT 中各新建一个用于学习的 Project，把 PROJECT_INSTRUCTIONS.md 里的那段话填进 Project 指令，并在其中启用 learning 连接器。

## 13. 实测清单

在每个客户端上依次检查。不通过的客户端记下来，以后不在上面学习。

1. 连接：能完成 GitHub 授权。claude.ai 网页端有已知的连接问题报告，重点看这一项。
2. 工具可见：说"开始学 cie6053"，模型应当调用 start_session。
3. 协作说明生效：在 Project 里学一小段，看讲解方式是否符合 AGENTS.md（比如简单内容不反复确认）；说"收尾"后，GitHub 仓库里应当出现一条"收尾"提交。
4. PDF：让模型用 read_source 读一份课件，说出其中某一页的内容。看不到 PDF 时，它应当按说明直接告诉你。
5. 权限（可选）：用另一个 GitHub 账号授权，工具应当无法使用。

## 14. 日常维护

- 服务端代码修改后：本地 commit 并 push，再在服务器上执行

  ```bash
  sudo -iu learning git -C /home/learning/learning pull
  sudo systemctl restart learning-mcp
  ```

  依赖有变化时，在 `server/` 下再运行一次 `uv sync`。
- 查看日志：`sudo journalctl -u learning-mcp -f`。
- 备份：状态和规则在 GitHub 上有完整历史；材料在笔记本和服务器上各有一份，并开启了版本控制。
