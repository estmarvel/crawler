"""千极链 AI 提取提供方配置。"""

from __future__ import annotations

import os
import re
from pathlib import Path


MODEL = "glm-5.2"
BASE_URL = "https://open.bigmodel.cn/api/paas/v4"
API_KEY_ENV = "ZHIPUAI_API_KEY"
PROJECT_ROOT = Path(__file__).resolve().parents[3]

_ENV_LINE = re.compile(r"^(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$")


def load_project_env(path: Path | None = None) -> Path:
    """加载本机 ``.env``，但绝不覆盖进程已经注入的环境变量。"""

    env_path = path or PROJECT_ROOT / ".env"
    if not env_path.is_file():
        return env_path
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        match = _ENV_LINE.match(line)
        if not match:
            continue
        key, value = match.groups()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        os.environ.setdefault(key, value)
    return env_path


__all__ = ["API_KEY_ENV", "BASE_URL", "MODEL", "load_project_env"]
