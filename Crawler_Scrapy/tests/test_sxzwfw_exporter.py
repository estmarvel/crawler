from __future__ import annotations

from pathlib import Path

from itemadapter import ItemAdapter

from crawler_scrapy.sites.sxzwfw.exporter import SxzwfwMultiFormatPipeline


def test_termination_subtype_exports_database_code_without_changing_tender_schema(tmp_path):
    pipeline = SxzwfwMultiFormatPipeline(output_root=Path(tmp_path))
    adapter = ItemAdapter(
        {
            "platform": "山西省公共资源交易平台",
            "platform_code": "sxzwfw",
            "notice_id": "1",
            "notice_type": "TENDER",
            "notice_subtype": "engineering.qt.zzgg",
            "title": "测试废标公告",
            "data": {},
            "attachments": [],
        }
    )

    record = pipeline._build_record(adapter, "招标公告")

    assert record["公告类型"] == "TERMINATION"
    assert "项目编号/招标编号" in record
