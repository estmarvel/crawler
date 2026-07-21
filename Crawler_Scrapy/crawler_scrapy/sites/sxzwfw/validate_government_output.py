"""校验山西政府采购小批量测试输出的数量、字段配对、快照和附件。"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Mapping

from crawler_scrapy.schemas.notice_fields import TYPE_OUTPUT_BASENAMES


TYPE_RULES = {
    "更正结果公示": (
        "公共类型",
        "项目名称",
        "公告内容",
        "发布日期",
        "详情页链接",
        "HTML快照路径",
    ),
    "中标结果公示": (
        "源站公告性质",
        "项目名称",
        "发布日期",
        "详情页链接",
        "HTML快照路径",
    ),
}


def _empty(value: Any) -> bool:
    return value is None or value == "" or value == [] or value == {}


def _load_rows(path: Path, errors: list[str]) -> list[dict[str, Any]]:
    if not path.exists():
        errors.append(f"缺少JSON文件：{path}")
        return []
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        errors.append(f"JSON无法读取：{path}: {exc}")
        return []
    if not isinstance(value, list):
        errors.append(f"JSON顶层不是数组：{path}")
        return []
    return [row for row in value if isinstance(row, dict)]


def _check_file(
    storage_root: Path,
    relative_path: Any,
    *,
    label: str,
    identity: str,
    errors: list[str],
) -> None:
    path_text = str(relative_path or "").strip()
    if not path_text:
        errors.append(f"{identity} 缺少{label}路径")
        return
    path = Path(path_text)
    resolved = path if path.is_absolute() else storage_root / path
    if not resolved.is_file() or resolved.stat().st_size <= 0:
        errors.append(f"{identity} {label}文件不存在或为空：{resolved}")


def _check_attachments(
    row: Mapping[str, Any],
    storage_root: Path,
    identity: str,
    errors: list[str],
) -> int:
    values = row.get("附件") or []
    if not isinstance(values, list):
        errors.append(f"{identity} 附件字段不是数组")
        return 0
    for index, attachment in enumerate(values, start=1):
        if not isinstance(attachment, Mapping):
            errors.append(f"{identity} 第{index}个附件不是对象")
            continue
        prefix = f"{identity} 第{index}个附件"
        if _empty(attachment.get("file_url")):
            errors.append(f"{prefix} 缺少file_url")
        _check_file(
            storage_root,
            attachment.get("storage_path"),
            label="附件",
            identity=prefix,
            errors=errors,
        )
        status = str(attachment.get("parse_status") or "")
        if status not in {"DOWNLOADED_NO_OCR", "CACHED_NO_OCR"}:
            errors.append(f"{prefix} 下载状态异常：{status or '空'}")
    return len(values)


def _check_award_pairs(
    row: Mapping[str, Any],
    identity: str,
    errors: list[str],
    warnings: list[str],
) -> None:
    names = row.get("中标人名称") or []
    prices = row.get("中标价") or []
    details = row.get("中标结果明细") or []
    if not all(isinstance(value, list) for value in (names, prices, details)):
        errors.append(f"{identity} 中标人、价格或明细字段不是数组")
        return
    if not details:
        warnings.append(f"{identity} 没有中标结果明细，需确认是否为废标/无成交结果")
        return
    detail_names = [value.get("中标人名称") for value in details if isinstance(value, Mapping)]
    detail_prices = [value.get("中标价") for value in details if isinstance(value, Mapping)]
    if len(details) != len(detail_names):
        errors.append(f"{identity} 中标结果明细包含非对象值")
        return
    if names != detail_names or prices != detail_prices:
        errors.append(f"{identity} 中标人/中标价数组与逐条明细不一致，可能发生错位")
    identities = [
        (
            str(value.get("标段") or ""),
            str(value.get("中标人名称") or ""),
            str(value.get("中标价") or ""),
        )
        for value in details
    ]
    if len(identities) != len(set(identities)):
        errors.append(f"{identity} 中标结果明细存在完全重复记录")


def _contact_warnings(
    notice_type: str,
    row: Mapping[str, Any],
    identity: str,
    warnings: list[str],
) -> None:
    body = str(row.get("公告正文") or row.get("公告内容") or "")
    if "采购人信息" in body:
        purchaser_fields = (
            ("招标人/采购人",) if notice_type == "中标结果公示" else ()
        ) + ("招标人联系方式",)
        missing = [field for field in purchaser_fields if _empty(row.get(field))]
        if missing:
            warnings.append(f"{identity} 正文包含采购人信息但字段为空：{','.join(missing)}")
    if "采购代理机构信息" in body:
        missing = [
            field
            for field in ("招标代理机构", "招标代理机构联系方式")
            if _empty(row.get(field))
        ]
        if missing:
            warnings.append(f"{identity} 正文包含代理机构信息但字段为空：{','.join(missing)}")


def validate_output(
    site_dir: Path,
    storage_root: Path,
    expected_per_type: int = 5,
) -> dict[str, Any]:
    errors: list[str] = []
    warnings: list[str] = []
    type_results: dict[str, Any] = {}
    total = 0
    attachment_total = 0

    for notice_type, required_fields in TYPE_RULES.items():
        basename = TYPE_OUTPUT_BASENAMES[notice_type]
        path = site_dir / "json" / f"{basename}.json"
        rows = _load_rows(path, errors)
        total += len(rows)
        if len(rows) != expected_per_type:
            errors.append(
                f"{notice_type} 实际{len(rows)}条，目标{expected_per_type}条"
            )

        for index, row in enumerate(rows, start=1):
            identity = f"{notice_type}第{index}条({row.get('公告ID') or row.get('公告标题') or '未知ID'})"
            missing = [field for field in required_fields if _empty(row.get(field))]
            if missing:
                errors.append(f"{identity} 缺少关键字段：{','.join(missing)}")
            if str(row.get("解析状态") or "") not in {"PARSED", "PARTIAL"}:
                errors.append(f"{identity} 解析状态异常：{row.get('解析状态')!r}")
            _check_file(
                storage_root,
                row.get("HTML快照路径"),
                label="HTML快照",
                identity=identity,
                errors=errors,
            )
            attachment_total += _check_attachments(
                row,
                storage_root,
                identity,
                errors,
            )
            _contact_warnings(notice_type, row, identity, warnings)
            if notice_type == "中标结果公示":
                _check_award_pairs(row, identity, errors, warnings)

        type_results[notice_type] = {
            "count": len(rows),
            "json_path": str(path),
        }

    return {
        "status": "PASS" if not errors else "FAIL",
        "expected_per_type": expected_per_type,
        "total_records": total,
        "attachment_records": attachment_total,
        "types": type_results,
        "errors": errors,
        "warnings": warnings,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("site_dir", type=Path, help="测试输出中的 sxzwfw 目录")
    parser.add_argument(
        "--storage-root",
        type=Path,
        required=True,
        help="NOTICE_OUTPUT_ROOT/FILES_STORE 根目录",
    )
    parser.add_argument("--expected", type=int, default=5, help="每种类型目标条数")
    parser.add_argument("--report", type=Path, help="校验报告 JSON 路径")
    args = parser.parse_args()

    report = validate_output(args.site_dir, args.storage_root, args.expected)
    rendered = json.dumps(report, ensure_ascii=False, indent=2)
    print(rendered)
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(rendered + "\n", encoding="utf-8")
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
