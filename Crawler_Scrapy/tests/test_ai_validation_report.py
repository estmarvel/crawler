from __future__ import annotations

import hashlib
import json

from crawler_scrapy.ai.validation_report import HTML_RE, _snapshot_ok


def test_html_residual_pattern_does_not_misclassify_business_notation():
    assert not HTML_RE.search("项目控制价增幅<2%")
    assert not HTML_RE.search("符合<政府采购法>及<<承装许可证>>")
    assert HTML_RE.search("<span>残留标签</span>")


def test_snapshot_audit_accepts_hashed_api_payload_when_html_is_absent(tmp_path):
    payload_path = tmp_path / "demo" / "payloads" / "01_招标计划" / "1.json"
    payload_path.parent.mkdir(parents=True)
    payload_path.write_text(json.dumps({"id": 1}), encoding="utf-8")
    digest = hashlib.sha256(payload_path.read_bytes()).hexdigest()
    row = {
        "HTML快照路径": "",
        "HTML快照SHA256": "",
        "_trace": {
            "payloadSnapshot": {
                "path": "demo/payloads/01_招标计划/1.json",
                "sha256": digest,
            }
        },
    }

    assert _snapshot_ok(tmp_path, row) == (True, "")
