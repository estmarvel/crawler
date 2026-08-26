"""Summarize isolated hybrid-AI validation output and its evidence trail."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable, Mapping

from crawler_scrapy.schemas.notice_fields import ANNOUNCEMENT_SCHEMAS, normalize_notice_type


# 只把合法的拉丁字母标签名视为 HTML。公告正文中的 ``<2%``、
# ``<政府采购法>`` 或双书名号 ``<<...>>`` 都是业务文本，不应误报。
HTML_RE = re.compile(
    r"<\s*/?[A-Za-z][A-Za-z0-9:-]*(?=\s|/?>)|&(?:nbsp|lt|gt|amp);",
    re.I,
)


def _rows(site_dir: Path) -> Iterable[tuple[Path, Mapping[str, Any]]]:
    for path in sorted((site_dir / "json").glob("*.json")):
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(value, list):
            for row in value:
                if isinstance(row, Mapping):
                    yield path, row


def _ai_trace(row: Mapping[str, Any]) -> tuple[str, Mapping[str, Any]]:
    field_meta = ((row.get("_trace") or {}).get("fieldMeta") or {})
    for key, value in field_meta.items():
        if str(key).endswith("HybridAi") and isinstance(value, Mapping):
            return str(key), value
    return "", {}


def _bucket(site: str, row: Mapping[str, Any]) -> str:
    if site == "qianji":
        subtype = str(row.get("公告子类型") or "")
        return subtype.split(".", 1)[0] or normalize_notice_type(row.get("公告类型"))
    return normalize_notice_type(row.get("公告类型"))


def _snapshot_ok(output_root: Path, row: Mapping[str, Any]) -> tuple[bool, str]:
    relative = str(row.get("HTML快照路径") or "").strip()
    expected = str(row.get("HTML快照SHA256") or "").strip()
    if relative and expected:
        path = output_root / relative
        if not path.is_file():
            return False, "missing_file"
        actual = hashlib.sha256(path.read_bytes()).hexdigest()
        return actual == expected, "" if actual == expected else "sha256_mismatch"

    # API-only 公告没有可保存的详情 HTML，但原始 JSON payload 同样可以作为
    # 可校验的源快照。只有 HTML 和 payload 都缺失时才报告溯源引用缺失。
    trace = row.get("_trace") or {}
    payload = trace.get("payloadSnapshot") or {}
    payload_relative = str(payload.get("path") or "").strip()
    payload_expected = str(payload.get("sha256") or "").strip()
    if not payload_relative or not payload_expected:
        return False, "missing_reference"
    payload_path = output_root / payload_relative
    if not payload_path.is_file():
        return False, "missing_file"
    payload_actual = hashlib.sha256(payload_path.read_bytes()).hexdigest()
    return (
        payload_actual == payload_expected,
        "" if payload_actual == payload_expected else "sha256_mismatch",
    )


def audit_site(output_root: Path, site: str) -> dict[str, Any]:
    site_dir = output_root / site
    counts: Counter[str] = Counter()
    statuses: Counter[str] = Counter()
    changed: Counter[str] = Counter()
    requested: Counter[str] = Counter()
    errors: Counter[str] = Counter()
    tokens = Counter()
    snapshot_errors: Counter[str] = Counter()
    html_residuals: Counter[str] = Counter()
    alignment_errors: Counter[str] = Counter()
    change_examples: list[dict[str, Any]] = []
    total = 0
    ai_called = 0

    for path, row in _rows(site_dir):
        total += 1
        bucket = _bucket(site, row)
        counts[bucket] += 1
        ok, snapshot_error = _snapshot_ok(output_root, row)
        if not ok:
            snapshot_errors[snapshot_error] += 1

        notice_type = normalize_notice_type(row.get("公告类型"))
        for field in ANNOUNCEMENT_SCHEMAS.get(notice_type, ()):
            value = row.get(field)
            if value not in (None, "", [], {}) and HTML_RE.search(
                json.dumps(value, ensure_ascii=False, default=str)
            ):
                html_residuals[field] += 1
        for names_field, prices_field in (
            ("中标候选人名称", "中标候选人报价"),
            ("定标候选人名称", "定标候选人报价"),
            ("中标人名称", "中标价"),
        ):
            names, prices = row.get(names_field), row.get(prices_field)
            if isinstance(names, list) and isinstance(prices, list) and len(names) != len(prices):
                alignment_errors[f"{names_field}/{prices_field}"] += 1

        _, trace = _ai_trace(row)
        if not trace:
            statuses["NOT_REQUESTED"] += 1
            continue
        status = str(trace.get("status") or "UNKNOWN")
        statuses[status] += 1
        calls = int(trace.get("calls") or 0)
        if calls:
            ai_called += 1
        usage = trace.get("tokenUsage") or {}
        for key in ("prompt", "completion", "total", "cachedPrompt"):
            tokens[key] += int(usage.get(key) or 0)
        for field in trace.get("requestedFields") or []:
            requested[str(field)] += 1
        for field in trace.get("filledFields") or []:
            changed[f"filled:{field}"] += 1
        for field in trace.get("replacedFields") or []:
            changed[f"replaced:{field}"] += 1
        error = str(trace.get("error") or "").strip()
        if error:
            errors[error.split(":", 1)[0]] += 1

        candidates = trace.get("candidates") or {}
        rule_values = trace.get("ruleValues") or {}
        final_values = trace.get("finalValues") or {}
        actions = {
            str(field): "filled" for field in trace.get("filledFields") or []
        }
        actions.update(
            {str(field): "replaced" for field in trace.get("replacedFields") or []}
        )
        for field, action in actions.items():
            candidate = candidates.get(field) or {}
            refs = candidate.get("evidenceRefs") or []
            change_examples.append(
                {
                    "site": site,
                    "bucket": bucket,
                    "file": str(path.relative_to(output_root)),
                    "noticeId": row.get("公告ID"),
                    "title": row.get("公告标题"),
                    "field": field,
                    "action": action,
                    "ruleValue": rule_values.get(field),
                    "finalValue": final_values.get(field, row.get(field)),
                    "grounded": bool(candidate.get("grounded")),
                    "confidence": candidate.get("confidence"),
                    "evidence": [ref.get("preview") for ref in refs if isinstance(ref, Mapping)],
                    "snapshot": row.get("HTML快照路径"),
                }
            )

    return {
        "site": site,
        "total": total,
        "counts": dict(counts),
        "aiCalledItems": ai_called,
        "statuses": dict(statuses),
        "tokens": dict(tokens),
        "requestedFields": dict(requested.most_common()),
        "changedFields": dict(changed.most_common()),
        "errors": dict(errors),
        "snapshotErrors": dict(snapshot_errors),
        "htmlResiduals": dict(html_residuals),
        "alignmentErrors": dict(alignment_errors),
        "changeExamples": change_examples,
    }


def _markdown(result: Mapping[str, Any]) -> str:
    lines = ["# Qwen3-8B 混合 AI 50 条/类型验收自动审计", ""]
    lines += [
        "| 站点 | 样本 | AI调用条目 | SUCCESS | FAILED | 填空/替换 | Token | 快照异常 | HTML残留 | 名单错位 |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for site in result["sites"]:
        status = site["statuses"]
        changes = site["changedFields"]
        filled = sum(value for key, value in changes.items() if key.startswith("filled:"))
        replaced = sum(value for key, value in changes.items() if key.startswith("replaced:"))
        lines.append(
            f"| {site['site']} | {site['total']} | {site['aiCalledItems']} | "
            f"{status.get('SUCCESS', 0)} | {status.get('FAILED', 0)} | "
            f"{filled}/{replaced} | {site['tokens'].get('total', 0)} | "
            f"{sum(site['snapshotErrors'].values())} | "
            f"{sum(site['htmlResiduals'].values())} | "
            f"{sum(site['alignmentErrors'].values())} |"
        )
    lines += ["", "## 各站类型数量", ""]
    for site in result["sites"]:
        values = "，".join(f"{key}={value}" for key, value in site["counts"].items())
        lines.append(f"- **{site['site']}**：{values or '无'}")
    lines += [
        "",
        "## 说明",
        "",
        "- `filled/replaced` 只证明 AI 改变了结果，不自动等于准确率提升。",
        "- 最终结论需使用同目录的 `change_examples.json` 对照快照证据人工审核。",
        "- 自动审计会检查快照 SHA256、HTML 残留以及候选人/报价等平行列表对齐。",
        "",
    ]
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--report-dir", type=Path)
    args = parser.parse_args()
    output_root = args.output_root.resolve()
    report_dir = (args.report_dir or (output_root / "validation_report")).resolve()
    report_dir.mkdir(parents=True, exist_ok=True)
    sites = sorted(
        path.name for path in output_root.iterdir() if path.is_dir() and (path / "json").is_dir()
    ) if output_root.is_dir() else []
    site_results = [audit_site(output_root, site) for site in sites]
    result = {"outputRoot": str(output_root), "sites": site_results}
    (report_dir / "summary.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    examples = [item for site in site_results for item in site["changeExamples"]]
    (report_dir / "change_examples.json").write_text(
        json.dumps(examples, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (report_dir / "README.md").write_text(_markdown(result), encoding="utf-8")
    print(f"站点={len(site_results)} 样本={sum(x['total'] for x in site_results)} AI改动={len(examples)}")
    print(report_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
