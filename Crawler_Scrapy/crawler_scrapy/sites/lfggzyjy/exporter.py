"""临汾公共资源交易平台独立导出路由。"""

from crawler_scrapy.sites.sxjm.exporter import SxjmMultiFormatPipeline


class LfggzyjyMultiFormatPipeline(SxjmMultiFormatPipeline):
    ROUTES = {
        "engineering.gcjs_tender_plan.zbjh": ("临汾公共资源_招标计划", "招标计划"),
        "engineering.gcjs_notice.zbgg": ("临汾公共资源_招标公告", "招标公告"),
        "engineering.gcjs_notice.gzjg": ("临汾公共资源_更正结果公示", "更正结果公示"),
        "engineering.gcjs_zbhxrgs.hxr": ("临汾公共资源_中标候选人公示", "中标候选人公示"),
        "engineering.gcjs_result_notice.zbjg": ("临汾公共资源_中标结果公示", "中标结果公示"),
    }

    @classmethod
    def _route(cls, subtype: str) -> str:
        return f"__lfggzyjy_{subtype}__"

    @classmethod
    def _route_config(cls, route: str) -> tuple[str, str]:
        subtype = route.removeprefix("__lfggzyjy_").removesuffix("__")
        try:
            return cls.ROUTES[subtype]
        except KeyError as exc:
            raise ValueError(f"未知临汾公共资源导出路由：{route}") from exc

