"""千极链按一级公告类型合并导出，结果更正单独使用 CORRECTION Schema。"""

from itemadapter import ItemAdapter

from crawler_scrapy.schemas.notice_fields import normalize_notice_type
from crawler_scrapy.sites.bitbid.exporter import BitbidMultiFormatPipeline


class QianjiMultiFormatPipeline(BitbidMultiFormatPipeline):
    # 工程/货物/服务仍是采集数据源和公告子类型，不再决定输出文件名。
    # 同一个一级公告类型必须共用同一个 route，避免多个 JSON 状态对象或
    # CSV 文件句柄同时写入同一路径。
    OUTPUT_ROUTES = {
        "plan": ("千极链_招标计划", "招标计划"),
        "tender": ("千极链_招标公告", "招标公告"),
        "change": ("千极链_变更公告", "招标公告"),
        "candidate": ("千极链_中标候选人公示", "中标候选人公示"),
        "award": ("千极链_结果公示", "中标结果公示"),
        "correction": ("千极链_更正结果公示", "更正结果公示"),
    }
    ROUTES = {
        "plan.all": OUTPUT_ROUTES["plan"],
        "tender.engineering": OUTPUT_ROUTES["tender"],
        "tender.goods": OUTPUT_ROUTES["tender"],
        "tender.service": OUTPUT_ROUTES["tender"],
        "change.engineering": OUTPUT_ROUTES["change"],
        "change.goods": OUTPUT_ROUTES["change"],
        "change.service": OUTPUT_ROUTES["change"],
        "candidate.engineering": OUTPUT_ROUTES["candidate"],
        "candidate.goods": OUTPUT_ROUTES["candidate"],
        "candidate.service": OUTPUT_ROUTES["candidate"],
        "award.engineering": OUTPUT_ROUTES["award"],
        "award.goods": OUTPUT_ROUTES["award"],
        "award.service": OUTPUT_ROUTES["award"],
    }

    @classmethod
    def _route(cls, subtype: str) -> str:
        category = subtype.partition(".")[0]
        if category not in cls.OUTPUT_ROUTES:
            raise ValueError(f"未知千极链公告子类型：{subtype}")
        return f"__qianji_{category}__"

    @classmethod
    def _route_config(cls, route: str) -> tuple[str, str]:
        category = route.removeprefix("__qianji_").removesuffix("__")
        try:
            return cls.OUTPUT_ROUTES[category]
        except KeyError as exc:
            raise ValueError(f"未知千极链导出路由：{route}") from exc

    @staticmethod
    def _is_termination(subtype: str, title: str) -> bool:
        return subtype.startswith("change.") and any(
            word in title for word in ("废标", "流标", "终止", "撤销", "暂停")
        )

    def process_item(self, item):
        adapter = ItemAdapter(item)
        subtype = str(adapter.get("notice_subtype") or "")
        if subtype not in self.ROUTES:
            return item
        actual_type = normalize_notice_type(adapter.get("notice_type"))
        if subtype.startswith("award.") and actual_type == "更正结果公示":
            route = "__qianji_correction__"
            schema_type = actual_type
        else:
            route = self._route(subtype)
            _, schema_type = self.ROUTES[subtype]
        if actual_type != schema_type:
            raise ValueError(
                f"千极链栏目与Schema不一致：subtype={subtype} type={actual_type}"
            )
        record = self._build_record(adapter, schema_type)
        if self._is_termination(subtype, str(adapter.get("title") or "")):
            # 变更栏目混有流标、撤销、暂停和终止公告；字段形状继续使用
            # 招标公告 Schema，数据库生命周期编码保存为 TERMINATION。
            record["公告类型"] = "TERMINATION"
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
        self.crawler.stats.inc_value(f"export/qianji/{subtype}")
        return item
