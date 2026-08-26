from __future__ import annotations

from pathlib import Path

from itemadapter import ItemAdapter

from crawler_scrapy.sites.sxzwfw.exporter import SxzwfwMultiFormatPipeline


def test_correction_subtype_uses_correction_schema_without_exporter_override(tmp_path):
    pipeline = SxzwfwMultiFormatPipeline(output_root=Path(tmp_path))
    adapter = ItemAdapter(
        {
            "platform": "山西省公共资源交易平台",
            "platform_code": "sxzwfw",
            "notice_id": "1",
            "notice_type": "CORRECTION",
            "notice_subtype": "engineering.qt.gzjg",
            "title": "测试废标公告",
            "data": {},
            "attachments": [],
        }
    )

    record = pipeline._build_record(adapter, "更正结果公示")

    assert record["公告类型"] == "CORRECTION"
    assert "公共类型" in record
