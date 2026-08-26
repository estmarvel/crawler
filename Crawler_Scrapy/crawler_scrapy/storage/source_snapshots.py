"""兼容读取新式独立快照与旧式内嵌 ``_trace`` 溯源数据。"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Mapping


def _safe_asset_path(output_root: Path, value: Any) -> Path | None:
    relative = str(value or "").strip()
    if not relative:
        return None
    root = output_root.expanduser().resolve()
    target = (root / relative).resolve()
    if target != root and root not in target.parents:
        raise ValueError(f"快照路径越出输出目录：{relative}")
    if not target.is_file():
        raise ValueError(f"快照文件不存在：{target}")
    return target


def _verified_bytes(
    output_root: Path,
    path_value: Any,
    sha256_value: Any,
) -> bytes | None:
    path = _safe_asset_path(output_root, path_value)
    if path is None:
        return None
    content = path.read_bytes()
    expected = str(sha256_value or "").strip().lower()
    actual = hashlib.sha256(content).hexdigest()
    if expected and actual != expected:
        raise ValueError(
            f"快照SHA256不一致：{path} expected={expected} actual={actual}"
        )
    return content


def load_record_payload(record: Mapping[str, Any], output_root: Path) -> Any:
    trace = record.get("_trace")
    if not isinstance(trace, Mapping):
        return None
    legacy = trace.get("payload")
    if legacy is not None:
        return legacy
    snapshot = trace.get("payloadSnapshot")
    if not isinstance(snapshot, Mapping):
        return None
    content = _verified_bytes(
        output_root,
        snapshot.get("path"),
        snapshot.get("sha256"),
    )
    if content is None:
        return None
    return json.loads(content.decode("utf-8"))


def load_record_html(record: Mapping[str, Any], output_root: Path) -> str:
    trace = record.get("_trace")
    if isinstance(trace, Mapping) and trace.get("rawHtml") is not None:
        return str(trace.get("rawHtml") or "")
    content = _verified_bytes(
        output_root,
        record.get("HTML快照路径"),
        record.get("HTML快照SHA256"),
    )
    if content is None:
        return ""
    for encoding in ("utf-8-sig", "gb18030"):
        try:
            return content.decode(encoding)
        except UnicodeDecodeError:
            continue
    return content.decode("utf-8", errors="replace")


def record_text(record: Mapping[str, Any]) -> str:
    value = record.get("公告正文") or record.get("公告内容")
    if value:
        return str(value)
    trace = record.get("_trace")
    return str(trace.get("rawText") or "") if isinstance(trace, Mapping) else ""
