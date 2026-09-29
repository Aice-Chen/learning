"""从环境变量读取配置。"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def _bool(value: str | None, default: bool) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    repo: Path
    transport: str = "http"
    host: str = "127.0.0.1"
    port: int = 8000
    base_url: str | None = None
    github_client_id: str | None = None
    github_client_secret: str | None = None
    jwt_signing_key: str | None = None
    allowed_github_user: str | None = None
    git_enabled: bool = True
    git_name: str = "learning-mcp"
    git_email: str = "learning-mcp@localhost"
    max_pdf_bytes: int = 30 * 1024 * 1024
    max_text_bytes: int = 2 * 1024 * 1024
    max_download_bytes: int = 200 * 1024 * 1024

    @property
    def auth_enabled(self) -> bool:
        return bool(self.github_client_id)

    @classmethod
    def from_env(cls) -> "Settings":
        env = os.environ
        default_repo = Path(__file__).resolve().parents[2]
        return cls(
            repo=Path(env.get("LEARNING_REPO", default_repo)).resolve(),
            transport=env.get("LEARNING_TRANSPORT", "http"),
            host=env.get("LEARNING_HOST", "127.0.0.1"),
            port=int(env.get("LEARNING_PORT", "8000")),
            base_url=env.get("LEARNING_BASE_URL"),
            github_client_id=env.get("LEARNING_GITHUB_CLIENT_ID"),
            github_client_secret=env.get("LEARNING_GITHUB_CLIENT_SECRET"),
            jwt_signing_key=env.get("LEARNING_JWT_SIGNING_KEY"),
            allowed_github_user=env.get("LEARNING_ALLOWED_GITHUB_USER"),
            git_enabled=_bool(env.get("LEARNING_GIT"), True),
            git_name=env.get("LEARNING_GIT_NAME", "learning-mcp"),
            git_email=env.get("LEARNING_GIT_EMAIL", "learning-mcp@localhost"),
        )

    def validate(self) -> None:
        """启动前检查：开启认证时，必须同时配置好放行的账号等参数。"""
        if self.auth_enabled:
            missing = [
                name
                for name, value in [
                    ("LEARNING_GITHUB_CLIENT_SECRET", self.github_client_secret),
                    ("LEARNING_BASE_URL", self.base_url),
                    ("LEARNING_JWT_SIGNING_KEY", self.jwt_signing_key),
                    ("LEARNING_ALLOWED_GITHUB_USER", self.allowed_github_user),
                ]
                if not value
            ]
            if missing:
                raise SystemExit("开启 GitHub 认证时还需要设置：" + "、".join(missing))
        elif self.transport == "http" and self.host not in {"127.0.0.1", "localhost", "::1"}:
            raise SystemExit(
                "未配置认证时，HTTP 服务只允许监听本机地址。"
                "对外提供服务前请设置 LEARNING_GITHUB_CLIENT_ID 等认证参数。"
            )
