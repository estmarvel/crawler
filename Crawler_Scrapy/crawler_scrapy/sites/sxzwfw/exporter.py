"""SXZWFW 导出补充：终止类编码和实时公告进度。"""

from __future__ import annotations

from itemadapter import ItemAdapter

from crawler_scrapy.pipelines import NoticeMultiFormatPipeline


class SxzwfwMultiFormatPipeline(NoticeMultiFormatPipeline):
    """保持公共八类字段文件，并纠正无独立 Schema 的终止公告编码。"""

    def _build_record(self, adapter: ItemAdapter, notice_type: str):
        record = super()._build_record(adapter, notice_type)
        subtype = str(adapter.get("notice_subtype") or "").lower()
        if subtype.split(".")[-1] == "zzgg":
            record["公告类型"] = "TERMINATION"
        return record

    def process_item(self, item):
        result = super().process_item(item)
        adapter = ItemAdapter(item)
        saved = self.crawler.stats.get_value("export/appended_versions", 0)
        every = self.crawler.settings.getint("SXZWFW_PROGRESS_EVERY", 0)
        if every > 0 and saved % every == 0:
            self.crawler.spider.logger.info(
                "[SXZWFW公告进度] 本次已保存=%s 公告ID=%s 源栏目=%s "
                "Schema=%s 标题=%s",
                saved,
                adapter.get("notice_id") or "",
                dict(adapter.get("field_meta") or {}).get("source_section") or "",
                adapter.get("notice_type") or "",
                str(adapter.get("title") or "")[:100],
            )
        return result
