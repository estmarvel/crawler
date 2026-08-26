"""中招联合（山西）按实际公告类型和工程/货物/服务导出。"""

from itemadapter import ItemAdapter

from crawler_scrapy.schemas.notice_fields import normalize_notice_type
from crawler_scrapy.sites.bitbid.exporter import BitbidMultiFormatPipeline

PROJECT_LABELS = {"engineering": "工程", "goods": "货物", "service": "服务"}
CATEGORY_ROUTES = {
    "tender": ("招标公告", "招标公告"),
    "correction": ("更正结果公示", "更正结果公示"),
    "candidate": ("中标候选人公示", "中标候选人公示"),
    "award": ("结果公示", "中标结果公示"),
}


class Trade365MultiFormatPipeline(BitbidMultiFormatPipeline):
    LABELS = PROJECT_LABELS
    CATEGORY = CATEGORY_ROUTES
    ROUTES = {
        f"{category}.{project_type}": (
            f"中招联合山西_{label}_{PROJECT_LABELS[project_type]}", schema
        )
        for category, (label, schema) in CATEGORY_ROUTES.items()
        for project_type in PROJECT_LABELS
    }

    @classmethod
    def _route(cls, subtype: str) -> str:
        return f"__trade365_{subtype}__"

    @classmethod
    def _route_config(cls, route: str) -> tuple[str, str]:
        subtype = route.removeprefix("__trade365_").removesuffix("__")
        try:
            return cls.ROUTES[subtype]
        except KeyError as exc:
            raise ValueError(f"未知中招联合山西导出路由：{route}") from exc

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
                f"中招联合山西栏目与Schema不一致：subtype={subtype} type={actual_type}"
            )
        record = self._build_record(adapter, schema_type)
        json_record = self._build_json_record(adapter, schema_type, record)
        self._write_formats(route, record, json_record)

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
        self.crawler.stats.inc_value(f"export/trade365/{subtype}")
        return item
