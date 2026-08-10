"""比比网专用导出器：四个栏目分别保存。"""

from itemadapter import ItemAdapter

from crawler_scrapy.schemas.notice_fields import normalize_notice_type
from crawler_scrapy.sites.sxjm.exporter import SxjmMultiFormatPipeline


class BitbidMultiFormatPipeline(SxjmMultiFormatPipeline):
    ROUTES = {
        "plan": ("比比网_招标计划", "招标计划"),
        "tender": ("比比网_招标公告", "招标公告"),
        "candidate": ("比比网_中标候选人公示", "中标候选人公示"),
        "award": ("比比网_中标结果公示", "中标结果公示"),
    }

    @classmethod
    def _route(cls, subtype: str) -> str:
        return f"__bitbid_{subtype}__"

    @classmethod
    def _route_config(cls, route: str) -> tuple[str, str]:
        subtype = route.removeprefix("__bitbid_").removesuffix("__")
        try:
            return cls.ROUTES[subtype]
        except KeyError as exc:
            raise ValueError(f"未知比比网导出路由：{route}") from exc

    def process_item(self, item):
        adapter = ItemAdapter(item)
        subtype = str(adapter.get("notice_subtype") or "")
        if subtype not in self.ROUTES:
            return item
        route = self._route(subtype)
        _, schema_type = self.ROUTES[subtype]
        actual_type = normalize_notice_type(adapter.get("notice_type"))
        if actual_type != schema_type:
            raise ValueError(
                f"比比网栏目与Schema不一致：subtype={subtype} type={actual_type}"
            )
        record = self._build_record(adapter, schema_type)
        if subtype == "award" and any(
            word in str(adapter.get("title") or "")
            for word in ("废标", "流标", "终止", "撤销")
        ):
            # 源站把废标结果混在“中标结果”栏目；字段形状仍复用结果公示，
            # 生命周期编码按数据库约定保存为 TERMINATION。
            record["公告类型"] = "TERMINATION"
        json_record = self._build_json_record(adapter, schema_type, record)
        csv_writer = self._get_csv_writer(route)
        self._get_json_path(route)
        self._append_json_record(route, json_record)
        csv_writer.writerow(
            {key: self._serialize_csv(value) for key, value in record.items()}
        )
        self._csv_files[route].flush()

        field_meta = dict(adapter.get("field_meta") or {})
        dedup = field_meta.get("_dedup") or {}
        store = getattr(self.crawler, "_notice_dedup_stores", {}).get(
            str(adapter.get("platform_code") or self.crawler.spider.platform_code)
        )
        if store is not None and dedup:
            store.commit(
                identity=dedup["identity"],
                content_fingerprint=dedup["content_fingerprint"],
                platform_code=str(adapter.get("platform_code") or ""),
                notice_id=str(adapter.get("notice_id") or ""),
                detail_url=str(adapter.get("detail_url") or ""),
                list_fingerprint=str(dedup.get("list_fingerprint") or ""),
            )
        self.crawler.stats.inc_value("export/appended_versions")
        self.crawler.stats.inc_value(f"export/bitbid/{subtype}")
        return item
