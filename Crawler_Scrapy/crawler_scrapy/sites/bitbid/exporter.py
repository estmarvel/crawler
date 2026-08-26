"""比比网专用导出器：源栏目按最终标准公告类型保存。"""

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
    DYNAMIC_ROUTES = {
        "__bitbid_prequalification__": (
            "比比网_资格预审公告", "资格预审公告"
        ),
        "__bitbid_finalization_candidate__": (
            "比比网_定标候选人公示", "定标候选人公示"
        ),
        "__bitbid_correction__": (
            "比比网_更正及其他公告", "更正结果公示"
        ),
    }

    @classmethod
    def _route(cls, subtype: str) -> str:
        return f"__bitbid_{subtype}__"

    @classmethod
    def _route_config(cls, route: str) -> tuple[str, str]:
        if route in cls.DYNAMIC_ROUTES:
            return cls.DYNAMIC_ROUTES[route]
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
        actual_type = normalize_notice_type(adapter.get("notice_type"))
        if actual_type == "资格预审公告" and subtype == "tender":
            route = "__bitbid_prequalification__"
            _, schema_type = self.DYNAMIC_ROUTES[route]
        elif actual_type == "定标候选人公示" and subtype == "tender":
            route = "__bitbid_finalization_candidate__"
            _, schema_type = self.DYNAMIC_ROUTES[route]
        elif actual_type == "更正结果公示" and subtype in {"tender", "award"}:
            route = "__bitbid_correction__"
            _, schema_type = self.DYNAMIC_ROUTES[route]
        elif actual_type == "中标候选人公示" and subtype == "tender":
            # 与源 candidate 栏目共享同一个路由键，避免两个文件句柄并发写同一路径。
            route = self._route("candidate")
            _, schema_type = self.ROUTES["candidate"]
        elif actual_type == "中标结果公示" and subtype == "tender":
            # 与源 award 栏目共享同一个路由键。
            route = self._route("award")
            _, schema_type = self.ROUTES["award"]
        else:
            route = self._route(subtype)
            _, schema_type = self.ROUTES[subtype]
        if actual_type != schema_type:
            raise ValueError(
                f"比比网栏目与Schema不一致：subtype={subtype} type={actual_type}"
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
        self.crawler.stats.inc_value(f"export/bitbid/{subtype}")
        return item
