"""不依赖数据库的公告身份与内容版本去重索引。

索引使用 JSON 文件保存：同一公告身份、同一内容指纹只导出一次；同一公告
内容发生变化时保留新的指纹，因此会作为新版本追加，历史版本不会被覆盖。
"""

from __future__ import annotations

import hashlib
import json
import threading
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import urlsplit, urlunsplit

from itemadapter import ItemAdapter


STATE_FORMAT_VERSION = 1
VOLATILE_CONTENT_FIELDS = frozenset(
    {
        "爬虫时间",
        "HTML快照路径",
        "HTML快照SHA256",
        "解析状态",
        "抽取方式",
        "抽取版本",
        "是否已核验",
    }
)
VOLATILE_LIST_FIELDS = frozenset(
    {
        # 浏览次数会因用户访问持续变化，但不代表公告正文或业务字段更新。
        "clickTimes",
        "clickCount",
        "viewCount",
        "browseCount",
        "readCount",
    }
)


def _json_value(value: Any) -> Any:
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, datetime):
        return value.isoformat(sep=" ", timespec="milliseconds")
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Mapping):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_value(item) for item in value]
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value


def _normalized_url(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    try:
        parts = urlsplit(text)
    except ValueError:
        return text
    # fragment 不会改变服务器内容；其余查询参数可能包含公告主键，必须保留。
    return urlunsplit(
        (
            parts.scheme.lower(),
            parts.netloc.lower(),
            parts.path,
            parts.query,
            "",
        )
    )


def build_notice_identity(
    *,
    platform_code: Any,
    notice_id: Any = "",
    detail_url: Any = "",
    notice_type: Any = "",
    title: Any = "",
    publish_time: Any = "",
) -> str:
    """按源站 ID、详情 URL、标题组合的优先级生成稳定公告身份。"""

    platform = str(platform_code or "unknown").strip().lower() or "unknown"
    source_id = str(notice_id or "").strip()
    if source_id:
        return f"{platform}|id:{source_id}"

    normalized_url = _normalized_url(detail_url)
    if normalized_url:
        return f"{platform}|url:{normalized_url}"

    fallback = "|".join(
        (
            str(notice_type or "").strip(),
            str(title or "").strip(),
            str(publish_time or "").strip(),
        )
    )
    digest = hashlib.sha256(fallback.encode("utf-8")).hexdigest()
    return f"{platform}|fallback:{digest}"


def build_list_fingerprint(record: Mapping[str, Any] | None) -> str:
    """计算列表记录指纹，用于内容未变化时跳过详情请求。"""

    if not record:
        return ""
    stable_record = {
        key: value
        for key, value in record.items()
        if key not in VOLATILE_LIST_FIELDS
    }
    payload = json.dumps(
        _json_value(stable_record),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def build_content_fingerprint(item_or_adapter: Any) -> str:
    """优先使用 Item 指纹；缺失时按正文、HTML、稳定业务数据依次计算。"""

    adapter = (
        item_or_adapter
        if isinstance(item_or_adapter, ItemAdapter)
        else ItemAdapter(item_or_adapter)
    )
    existing = str(adapter.get("fingerprint") or "").strip()
    if existing:
        return existing

    raw_text = str(adapter.get("raw_text") or "").strip()
    if raw_text:
        return hashlib.sha256(raw_text.encode("utf-8")).hexdigest()

    raw_html = adapter.get("raw_html")
    if isinstance(raw_html, bytes):
        raw_bytes = raw_html
    elif isinstance(raw_html, (bytearray, memoryview)):
        raw_bytes = bytes(raw_html)
    elif raw_html not in (None, ""):
        raw_bytes = str(raw_html).encode("utf-8")
    else:
        raw_bytes = b""
    if raw_bytes:
        return hashlib.sha256(raw_bytes).hexdigest()

    stable_data = {
        key: value
        for key, value in dict(adapter.get("data") or {}).items()
        if key not in VOLATILE_CONTENT_FIELDS
    }
    payload = json.dumps(
        _json_value(stable_data),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest() if payload else ""


class JsonNoticeDedupStore:
    """线程安全的 JSON 公告版本索引；适用于单个 Scrapy 进程。"""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self._lock = threading.RLock()
        self._pending: set[tuple[str, str]] = set()
        self._state = self._load()

    def _load(self) -> dict[str, Any]:
        if not self.path.exists() or self.path.stat().st_size == 0:
            return {
                "format_version": STATE_FORMAT_VERSION,
                "updated_at": "",
                "identities": {},
            }
        try:
            value = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise RuntimeError(f"公告去重索引无法读取：{self.path}: {exc}") from exc
        if not isinstance(value, dict) or not isinstance(
            value.get("identities"), dict
        ):
            raise RuntimeError(f"公告去重索引格式错误：{self.path}")
        return value

    @property
    def identity_count(self) -> int:
        with self._lock:
            return len(self._state["identities"])

    def has_identity(self, identity: str) -> bool:
        """返回源站公告身份是否已经成功导出过。"""

        with self._lock:
            return identity in self._state["identities"]

    def should_fetch_detail(self, identity: str, list_fingerprint: str) -> bool:
        """新公告或列表记录发生变化时请求详情；旧索引无列表指纹时复查一次。"""

        with self._lock:
            entry = self._state["identities"].get(identity)
            if not entry:
                return True
            previous = str(entry.get("list_fingerprint") or "")
            return not list_fingerprint or not previous or previous != list_fingerprint

    def reserve(self, identity: str, content_fingerprint: str) -> bool:
        """为当前进程预留版本；已保存或已预留返回 False。"""

        version = (identity, content_fingerprint)
        with self._lock:
            entry = self._state["identities"].get(identity) or {}
            fingerprints = set(entry.get("content_fingerprints") or [])
            if content_fingerprint in fingerprints or version in self._pending:
                return False
            self._pending.add(version)
            return True

    def release(self, identity: str, content_fingerprint: str) -> None:
        with self._lock:
            self._pending.discard((identity, content_fingerprint))

    def commit(
        self,
        *,
        identity: str,
        content_fingerprint: str,
        platform_code: str = "",
        notice_id: str = "",
        detail_url: str = "",
        list_fingerprint: str = "",
        save: bool = True,
    ) -> None:
        """在 CSV 和 JSON 均写入成功后提交新版本。"""

        now = datetime.now().isoformat(sep=" ", timespec="seconds")
        with self._lock:
            entry = self._state["identities"].setdefault(
                identity,
                {
                    "platform_code": platform_code,
                    "notice_id": notice_id,
                    "detail_url": detail_url,
                    "first_seen_at": now,
                    "last_seen_at": now,
                    "list_fingerprint": "",
                    "content_fingerprints": [],
                },
            )
            fingerprints = entry.setdefault("content_fingerprints", [])
            if content_fingerprint and content_fingerprint not in fingerprints:
                fingerprints.append(content_fingerprint)
            entry["last_seen_at"] = now
            if list_fingerprint:
                entry["list_fingerprint"] = list_fingerprint
            if detail_url and not entry.get("detail_url"):
                entry["detail_url"] = detail_url
            self._pending.discard((identity, content_fingerprint))
            if save:
                self._save_locked()

    def update_list_fingerprint(self, identity: str, value: str) -> None:
        """详情内容未变时更新列表指纹，避免下一次仍重复请求详情。"""

        if not value:
            return
        with self._lock:
            entry = self._state["identities"].get(identity)
            if not entry or entry.get("list_fingerprint") == value:
                return
            entry["list_fingerprint"] = value
            entry["last_seen_at"] = datetime.now().isoformat(
                sep=" ", timespec="seconds"
            )
            self._save_locked()

    def bootstrap_from_json_exports(self, json_dir: Path) -> int:
        """首次启用时从现有导出文件建立索引，历史结果不会被再次追加。"""

        if self.identity_count or not json_dir.exists():
            return 0
        imported = 0
        for path in sorted(json_dir.glob("*.json")):
            try:
                rows = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError) as exc:
                raise RuntimeError(f"现有JSON结果无法读取，拒绝覆盖：{path}: {exc}") from exc
            if not isinstance(rows, list):
                raise RuntimeError(f"现有JSON结果不是数组，拒绝覆盖：{path}")
            for row in rows:
                if not isinstance(row, Mapping):
                    continue
                identity = build_notice_identity(
                    platform_code=row.get("平台代码") or "unknown",
                    notice_id=row.get("公告ID"),
                    detail_url=row.get("详情页链接"),
                    notice_type=row.get("公告类型"),
                    title=row.get("公告标题"),
                    publish_time=row.get("发布时间") or row.get("发布日期"),
                )
                content_fingerprint = str(row.get("内容指纹") or "").strip()
                if not content_fingerprint:
                    stable_row = {
                        key: value
                        for key, value in row.items()
                        if key not in VOLATILE_CONTENT_FIELDS
                    }
                    payload = json.dumps(
                        _json_value(stable_row),
                        ensure_ascii=False,
                        sort_keys=True,
                        separators=(",", ":"),
                    )
                    content_fingerprint = hashlib.sha256(
                        payload.encode("utf-8")
                    ).hexdigest()
                self.commit(
                    identity=identity,
                    content_fingerprint=content_fingerprint,
                    platform_code=str(row.get("平台代码") or ""),
                    notice_id=str(row.get("公告ID") or ""),
                    detail_url=str(row.get("详情页链接") or ""),
                    save=False,
                )
                imported += 1
        if imported:
            with self._lock:
                self._save_locked()
        return imported

    def _save_locked(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._state["format_version"] = STATE_FORMAT_VERSION
        self._state["updated_at"] = datetime.now().isoformat(
            sep=" ", timespec="seconds"
        )
        temp_path = self.path.with_suffix(self.path.suffix + ".tmp")
        temp_path.write_text(
            json.dumps(self._state, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        temp_path.replace(self.path)


def get_notice_dedup_store(
    crawler: Any,
    *,
    output_root: Path,
    platform_code: str,
) -> JsonNoticeDedupStore:
    """同一 Crawler、同一网站共享一个索引实例。"""

    stores = getattr(crawler, "_notice_dedup_stores", None)
    if stores is None:
        stores = {}
        setattr(crawler, "_notice_dedup_stores", stores)
    code = str(platform_code or "unknown").strip() or "unknown"
    if code not in stores:
        stores[code] = JsonNoticeDedupStore(
            Path(output_root) / code / "state" / "notice_versions.json"
        )
    return stores[code]
