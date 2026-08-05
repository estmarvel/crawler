"""八类招投标公告的统一字段规范。

字段来源：项目爬取关键字段20260622.xlsx。

设计原则：
1. Excel 要求的业务字段及少量有业务含义的扩展字段统一放在 NoticeItem.data 中；
2. 各网站可以使用不同提取逻辑，但最终必须转换成这里的字段名；
3. 字段列表保持固定顺序，便于 CSV、JSON 导出和后续数据库映射；
4. 爬虫/数据库元数据只保存在 NoticeItem 顶层，导出时统一追加一次，不再
   重复塞入每一种业务 Schema；
5. HTML 原文保存在独立快照和 JSON 溯源包中，并记录路径与 SHA256；
6. 对旧 settings.py / crawler.py 中的字段名提供兼容映射。
"""

from __future__ import annotations

import re
from collections import OrderedDict
from datetime import date, datetime, time
from decimal import Decimal, InvalidOperation
from typing import Any, Final, Mapping


NOTICE_SCHEMA_VERSION: Final[str] = "2026-08-05-v8"

# 数据库中已经存在、并且可由爬虫或规则解析器直接产出的存储元数据字段。
# 它们不是公告业务字段，不能参与业务字段缺失率或被存入 extractedFields。
DATABASE_CRAWLER_FIELDS: Final[tuple[str, ...]] = (
    "公告正文",
    "解析状态",
    "内容指纹",
    "抽取方式",
    "抽取版本",
    "是否已核验",
)

# JSON/CSV 传输层使用的公共存储字段。“附件”固定为最后一个字段。
# 保留 SYSTEM_FIELDS 名称是为了兼容现有导出器和 AI 排除列表；
# ANNOUNCEMENT_SCHEMAS 自 v7 起不再包含这些字段。
SYSTEM_FIELDS: Final[tuple[str, ...]] = (
    *DATABASE_CRAWLER_FIELDS,
    "爬虫时间",
    "详情页链接",
    "HTML快照路径",
    "HTML快照SHA256",
    "附件",
)


def _business_fields(*fields: str) -> tuple[str, ...]:
    """返回纯业务字段，并保证没有重复字段名。"""

    result = tuple(fields)
    if len(result) != len(set(result)):
        raise ValueError(f"公告字段存在重复项：{result}")
    return result


# 原始表格中“资格预审公告”和“招标公告”的“招标代理机构”各出现两次。
# 按要求只保留第二个，即联系方式区域中的“招标代理机构”。
# 旧代码中的“招标代理机构(名称)”通过 FIELD_ALIASES 兼容映射，不再独立输出。
ANNOUNCEMENT_SCHEMAS: Final["OrderedDict[str, tuple[str, ...]]"] = OrderedDict(
    {
        "招标计划": _business_fields(
            "项目性质",
            "招标方式",
            "项目名称",
            "项目编号",
            "招标编号",
            "项目类型",
            "项目总投资",
            "招标内容",
            "招标人名称",
            "行政监督部门",
            "建设地点",
            "建设内容及规模",
            "招标公告（资格预审公告）预计发布时间",
            "发布日期",
            "发布网站",
        ),
        "资格预审公告": _business_fields(
            "项目性质",
            "项目名称",
            "项目编号",
            "招标编号",
            "所属行业",
            "组织形式",
            "开标时间",
            "项目编号/招标编号",
            "项目类型/行业分类",
            "项目总投资/估算金额",
            "招标金额",
            "资金来源",
            "项目地点",
            "招标人/采购人名称",
            "项目概况与招标范围",
            "申请人资格要求/投标人资格要求",
            "预审文件获取时间",
            "获取方式",
            "递交截止时间",
            "递交方法",
            "开启时间",
            "开启方式",
            "开启地点",
            "评审办法",
            "投标保证金方式",
            "招标人地址",
            "招标人联系人",
            "招标人联系方式",
            "招标代理机构",
            "招标代理机构地址",
            "招标代理机构联系人",
            "招标代理机构联系方式",
            "发布日期",
            "发布网站",
        ),
        "招标公告": _business_fields(
            "项目性质",
            "项目名称",
            "项目编号",
            "招标编号",
            "所属行业",
            "组织形式",
            "开标时间",
            "项目编号/招标编号",
            "项目类型/行业分类",
            "项目总投资/估算金额",
            "招标金额",
            "资金来源",
            "项目地点",
            "招标人/采购人名称",
            "项目规模",
            "工期/服务期/供货日期",
            "质量要求",
            "招标内容与范围",
            "申请人资格要求/投标人资格要求",
            "预审文件获取时间",
            "获取方式",
            "递交截止时间",
            "递交方法",
            "开启时间",
            "开启方式",
            "开启地点",
            "评审办法",
            "投标保证金方式",
            "招标人地址",
            "招标人联系人",
            "招标人联系方式",
            "招标代理机构",
            "招标代理机构地址",
            "招标代理机构联系人",
            "招标代理机构联系方式",
            "发布日期",
            "发布网站",
        ),
        "中标候选人公示": _business_fields(
            "项目性质",
            "项目名称",
            "项目编号",
            "招标编号",
            "所属行业",
            "组织形式",
            "开标时间",
            "公示时间",
            "招标编号/项目编号",
            "中标候选人名称",
            "中标候选人报价",
            "招标人/采购人",
            "招标人地址",
            "招标人联系人",
            "招标人联系方式",
            "招标代理机构",
            "招标代理机构地址",
            "招标代理机构联系人",
            "招标代理机构联系方式",
            "发布日期",
            "发布网站",
        ),
        "定标候选人公示": _business_fields(
            "项目性质",
            "项目名称",
            "项目编号",
            "招标编号",
            "所属行业",
            "组织形式",
            "开标时间",
            "公示时间",
            "招标编号/项目编号",
            "定标候选人名称",
            "定标候选人报价",
            "定标候选人项目经理",
            "定标候选人项目经理相关证书及编号",
            "定标候选人项目副经理",
            "定标候选人项目副经理相关证书及编号",
            "定标候选人资信情况",
            "定标候选人业绩情况（名称、日期、金额）",
            "招标人/采购人",
            "招标人地址",
            "招标人联系人",
            "招标人联系方式",
            "招标代理机构",
            "招标代理机构地址",
            "招标代理机构联系人",
            "招标代理机构联系方式",
            "依据文件",
            "依据文号",
            "发布日期",
            "发布网站",
        ),
        "中标结果公示": _business_fields(
            "项目性质",
            "项目名称",
            "项目编号",
            "招标编号",
            "所属行业",
            "组织形式",
            "招标方式",
            "中标人名称",
            "联合体成员",
            "中标价",
            "工期",
            "项目经理",
            "项目经理证书名称",
            "项目经理证书编号",
            "招标人/采购人",
            "招标人地址",
            "招标人联系人",
            "招标人联系方式",
            "招标代理机构",
            "招标代理机构地址",
            "招标代理机构联系人",
            "招标代理机构联系方式",
            "依据文件",
            "依据文号",
            "发布日期",
            "发布网站",
        ),
        "更正结果公示": _business_fields(
            "公共类型",
            "项目名称",
            "项目编号",
            "招标编号",
            "所属行业",
            "组织形式",
            "开标时间",
            "标书发售时间",
            "公告内容",
            "招标人地址",
            "招标人联系人",
            "招标人联系方式",
            "招标代理机构",
            "招标代理机构地址",
            "招标代理机构联系人",
            "招标代理机构联系方式",
            "监督部门地址",
            "监督部门联系人",
            "监督部门联系方式",
            "依据文件",
            "依据文号",
            "发布日期",
            "发布网站",
        ),
        "合同与履约": _business_fields(
            "项目名称",
            "项目编号",
            "招标编号",
            "合同名称",
            "招标人名称",
            "中标人名称",
            "合同金额",
            "合同期限",
            "合同签署时间",
            "合同主要内容",
            "发布日期",
            "发布网站",
        ),
    }
)

ANNOUNCEMENT_TYPES: Final[tuple[str, ...]] = tuple(ANNOUNCEMENT_SCHEMAS.keys())

# 数据库 project_notice.notice_type / notice_extraction.notice_type 使用的标准编码。
NOTICE_TYPE_CODES: Final[dict[str, str]] = {
    "招标计划": "PLAN",
    "资格预审公告": "PREQUALIFICATION",
    "招标公告": "TENDER",
    "中标候选人公示": "CANDIDATE",
    "定标候选人公示": "FINAL_CANDIDATE",
    "中标结果公示": "AWARD",
    "更正结果公示": "CORRECTION",
    "合同与履约": "CONTRACT",
}
NOTICE_TYPE_NAMES: Final[dict[str, str]] = {
    code: name for name, code in NOTICE_TYPE_CODES.items()
}

# 这些字段在数据库中有明确的非字符串类型。其他业务字段仍保留原文，
# 后续即使数据库增加新列，也不会因为当前阶段的过早转换丢失信息。
DATETIME_FIELDS: Final[frozenset[str]] = frozenset(
    {
        "爬虫时间",
        "发布日期",
        "开标时间",
    }
)
DATE_FIELDS: Final[frozenset[str]] = frozenset({"合同签署时间"})
DECIMAL_FIELDS: Final[frozenset[str]] = frozenset(
    {
        "项目总投资",
        "项目总投资/估算金额",
        "招标金额",
        "合同金额",
    }
)
STRING_LIST_FIELDS: Final[frozenset[str]] = frozenset(
    {
        "中标候选人名称",
        "定标候选人名称",
        "中标人名称",
        "联合体成员",
    }
)
AMOUNT_LIST_FIELDS: Final[frozenset[str]] = frozenset(
    {
        "中标候选人报价",
        "定标候选人报价",
        "中标价",
    }
)
OBJECT_LIST_FIELDS: Final[frozenset[str]] = frozenset(
    {"中标候选人明细", "中标结果明细"}
)
BOOLEAN_FIELDS: Final[frozenset[str]] = frozenset({"是否已核验"})

# 仅供站点解析、路由判断和质量诊断使用，不属于数据库业务字段，也不会由
# NoticeSchemaPipeline 写入最终 data/CSV/JSON 主字段。原始值仍在 raw_data/_trace。
PARSER_DIAGNOSTIC_FIELDS: Final[dict[str, tuple[str, ...]]] = {
    "资格预审公告": ("源站公告性质",),
    "招标公告": ("源站公告性质",),
    "中标候选人公示": ("源站公告性质", "中标候选人明细"),
    "中标结果公示": ("源站公告性质", "中标结果明细"),
}


NOTICE_TYPE_ALIASES: Final[dict[str, str]] = {
    "zbjh": "招标计划",
    "招标计划公告": "招标计划",
    "zbys": "资格预审公告",
    "资格预审": "资格预审公告",
    "zbgg": "招标公告",
    "招标资审公告": "招标公告",
    "hxr": "中标候选人公示",
    "候选公示": "中标候选人公示",
    "中标候选人公告": "中标候选人公示",
    "dbhxr": "定标候选人公示",
    "定标候选人": "定标候选人公示",
    "zbjg": "中标结果公示",
    "中标结果": "中标结果公示",
    "中标结果公告": "中标结果公示",
    "gzjg": "更正结果公示",
    "更正中标结果公示": "更正结果公示",
    "撤销中标结果公示": "更正结果公示",
    "更正/撤销中标结果公示": "更正结果公示",
    "htly": "合同与履约",
    "合同履约": "合同与履约",
}


COMMON_FIELD_ALIASES: Final[dict[str, str]] = {
    "抓取时间": "爬虫时间",
    "爬取时间": "爬虫时间",
    "详情页面": "详情页链接",
    "详情链接": "详情页链接",
    "快照路径": "HTML快照路径",
    "HTML原文路径": "HTML快照路径",
    "快照SHA256": "HTML快照SHA256",
    "附件信息": "附件",
}


FIELD_ALIASES: Final[dict[str, Mapping[str, str]]] = {
    "资格预审公告": {
        "招标代理机构(名称)": "招标代理机构",
    },
    "招标公告": {
        "招标代理机构(名称)": "招标代理机构",
        "招标文件获取时间": "预审文件获取时间",
    },
    "中标结果公示": {
        "中标价格": "中标价",
    },
    "更正结果公示": {
        "公告类型": "公共类型",
    },
}


# 存储元数据已经移出业务 Schema；保留空集合兼容缺失字段计算接口。
COMMON_OPTIONAL_FIELDS: Final[frozenset[str]] = frozenset(
    {
        # 招标编号是八类新增关联字段，源公告可能不公开；旧的组合字段继续
        # 保留，兼容项目关键字段表和既有数据库导入逻辑。
        "招标编号",
    }
)

OPTIONAL_FIELDS: Final[dict[str, frozenset[str]]] = {
    # 除合同外，“项目编号”是本次为关联补充的扩展字段；合同 Schema 中的
    # 项目编号原本就是 Excel 必需字段，不能在这里降级为可选。
    "招标计划": frozenset({"项目编号"}),
    "资格预审公告": frozenset({"项目编号"}),
    "招标公告": frozenset({"项目编号"}),
    "中标候选人公示": frozenset({"项目编号"}),
    "定标候选人公示": frozenset(
        {
            "项目编号",
            "定标候选人项目经理",
            "定标候选人项目经理相关证书及编号",
            "定标候选人项目副经理",
            "定标候选人项目副经理相关证书及编号",
            "定标候选人资信情况",
            "定标候选人业绩情况（名称、日期、金额）",
            "依据文件",
            "依据文号",
        }
    ),
    "中标结果公示": frozenset(
        {
            "项目编号",
            "联合体成员",
            "项目经理",
            "项目经理证书名称",
            "项目经理证书编号",
            "依据文件",
            "依据文号",
        }
    ),
    "更正结果公示": frozenset({"项目编号"}),
}


TYPE_OUTPUT_BASENAMES: Final[dict[str, str]] = {
    "招标计划": "01_招标计划",
    "资格预审公告": "02_资格预审公告",
    "招标公告": "03_招标公告",
    "中标候选人公示": "04_中标候选人公示",
    "定标候选人公示": "05_定标候选人公示",
    "中标结果公示": "06_中标结果公示",
    "更正结果公示": "07_更正结果公示",
    "合同与履约": "08_合同与履约",
}


def normalize_notice_type(notice_type: Any) -> str:
    """把网站子类型、中文别名统一成八类标准公告名称。"""

    value = str(notice_type or "").strip()
    if value in ANNOUNCEMENT_SCHEMAS:
        return value
    if value.upper() in NOTICE_TYPE_NAMES:
        return NOTICE_TYPE_NAMES[value.upper()]
    return NOTICE_TYPE_ALIASES.get(value, value)


def get_notice_type_code(notice_type: Any) -> str:
    """把中文公告类型或网站别名转换为数据库使用的标准编码。"""

    normalized_type = normalize_notice_type(notice_type)
    return NOTICE_TYPE_CODES.get(normalized_type, "")


def get_notice_fields(notice_type: Any) -> tuple[str, ...]:
    """取得公告类型对应的固定字段顺序。未知类型返回空元组。"""

    normalized_type = normalize_notice_type(notice_type)
    return ANNOUNCEMENT_SCHEMAS.get(normalized_type, ())


def coerce_datetime(value: Any) -> datetime | None:
    """把常见网站时间转换为可写入 MySQL DATETIME(3) 的 datetime。"""

    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        return value.replace(tzinfo=None) if value.tzinfo else value
    if isinstance(value, date):
        return datetime.combine(value, time.min)

    text = str(value).strip()
    if not text:
        return None
    normalized = text.replace("年", "-").replace("月", "-").replace("日", "")
    normalized = normalized.replace("/", "-").replace("T", " ")
    normalized = re.sub(r"\s+", " ", normalized).strip()
    if normalized.endswith("Z"):
        normalized = normalized[:-1] + "+00:00"

    try:
        parsed = datetime.fromisoformat(normalized)
        return parsed.replace(tzinfo=None) if parsed.tzinfo else parsed
    except ValueError:
        pass

    for fmt in (
        "%Y-%m-%d %H:%M:%S.%f",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d %H:%M",
        "%Y-%m-%d",
    ):
        try:
            return datetime.strptime(normalized, fmt)
        except ValueError:
            continue
    return None


def coerce_date(value: Any) -> date | None:
    """把网站日期转换为可写入 MySQL DATE 的 date。"""

    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    parsed = coerce_datetime(value)
    return parsed.date() if parsed else None


_AMOUNT_NUMBER_RE: Final[re.Pattern[str]] = re.compile(
    r"[-+]?\d[\d,，]*(?:\.\d+)?"
)


def coerce_decimal_amount(value: Any) -> Decimal | None:
    """把人民币金额统一换算成元并保留两位小数。

    百分比、单价、费率和无法确定单位的非纯数字文本不强制转换，调用方可
    继续保留原文，避免把 68% 错写成 68 元。
    """

    if value in (None, ""):
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, Decimal):
        return value.quantize(Decimal("0.01"))
    if isinstance(value, (int, float)):
        return Decimal(str(value)).quantize(Decimal("0.01"))

    text = str(value).strip()
    if not text or any(marker in text for marker in ("%", "％", "单价", "费率", "折扣")):
        return None

    matches = _AMOUNT_NUMBER_RE.findall(text)
    if not matches:
        return None
    number_text = matches[-1].replace(",", "").replace("，", "")
    try:
        amount = Decimal(number_text)
    except InvalidOperation:
        return None

    if "亿元" in text:
        amount *= Decimal("100000000")
    elif "万元" in text or re.search(r"(?:^|\s)万(?:\s|$)", text):
        amount *= Decimal("10000")
    return amount.quantize(Decimal("0.01"))


def _coerce_string_list(value: Any) -> list[str]:
    if value in (None, "", []):
        return []
    if isinstance(value, str):
        return [part.strip() for part in value.splitlines() if part.strip()]
    if isinstance(value, (list, tuple, set)):
        return [str(part).strip() for part in value if str(part).strip()]
    return [str(value).strip()]


def _coerce_amount_list(
    value: Any,
    *,
    preserve_positions: bool = False,
) -> list[Decimal | str | None]:
    if value in (None, "", []):
        return []
    values = value if isinstance(value, (list, tuple)) else str(value).splitlines()
    result: list[Decimal | str | None] = []
    for item in values:
        if item in (None, ""):
            if preserve_positions:
                result.append(None)
            continue
        amount = coerce_decimal_amount(item)
        result.append(amount if amount is not None else str(item).strip())
    return result


def _coerce_candidate_detail_list(value: Any) -> list[dict[str, Any]]:
    """规范候选人明细，同时保持企业、标段和报价处于同一条记录。"""

    if not isinstance(value, (list, tuple)):
        return []
    result: list[dict[str, Any]] = []
    for item in value:
        if not isinstance(item, Mapping):
            continue
        name = str(item.get("候选人名称") or "").strip()
        if not name:
            continue
        raw_amount = item.get("候选人报价")
        amount = coerce_decimal_amount(raw_amount)
        if amount is None and raw_amount not in (None, ""):
            amount = str(raw_amount).strip()
        result.append(
            {
                "标段": str(item.get("标段") or "").strip(),
                "候选人名称": name,
                "候选人报价": amount,
            }
        )
    return result


def _coerce_award_detail_list(value: Any) -> list[dict[str, Any]]:
    """规范中标结果明细，保证标段、中标人和中标价始终处于同一条记录。"""

    if not isinstance(value, (list, tuple)):
        return []
    result: list[dict[str, Any]] = []
    for item in value:
        if not isinstance(item, Mapping):
            continue
        name = str(item.get("中标人名称") or "").strip()
        if not name:
            continue
        raw_amount = item.get("中标价")
        amount = coerce_decimal_amount(raw_amount)
        if amount is None and raw_amount not in (None, ""):
            amount = str(raw_amount).strip()
        result.append(
            {
                "标段": str(item.get("标段") or "").strip(),
                "中标人名称": name,
                "中标价": amount,
            }
        )
    return result


def canonicalize_attachment_list(value: Any) -> list[dict[str, Any]]:
    """把各网站附件元数据统一成数据库附件表兼容的字段和类型。"""

    if value in (None, ""):
        return []
    values = [value] if isinstance(value, Mapping) else value
    if not isinstance(values, (list, tuple)):
        return []

    result: list[dict[str, Any]] = []
    for item in values:
        if not isinstance(item, Mapping):
            continue
        file_size = item.get("file_size_bytes", item.get("size"))
        try:
            file_size = int(file_size) if file_size not in (None, "") else None
        except (TypeError, ValueError):
            file_size = None
        result.append(
            {
                # source_file_id 当前数据库尚无对应列，但爬虫必须保留源站文件标识。
                "source_file_id": str(
                    item.get("source_file_id") or item.get("file_id") or ""
                ).strip()
                or None,
                "file_name": str(
                    item.get("file_name") or item.get("name") or ""
                ).strip()
                or None,
                "file_url": str(
                    item.get("file_url")
                    or item.get("download_url")
                    or item.get("url")
                    or ""
                ).strip()
                or None,
                "storage_path": str(item.get("storage_path") or "").strip() or None,
                "file_hash": str(
                    item.get("file_hash") or item.get("checksum") or ""
                ).strip()
                or None,
                "file_size_bytes": file_size,
                "file_type": str(item.get("file_type") or "").strip() or None,
                "parse_status": str(item.get("parse_status") or "PENDING").strip(),
            }
        )
    return result


def _empty_value_for_field(field: str) -> Any:
    if (
        field == "附件"
        or field in STRING_LIST_FIELDS
        or field in AMOUNT_LIST_FIELDS
        or field in OBJECT_LIST_FIELDS
    ):
        return []
    if field == "解析状态":
        return "PENDING"
    if field in BOOLEAN_FIELDS:
        return False
    if field in DATETIME_FIELDS or field in DATE_FIELDS or field in DECIMAL_FIELDS:
        return None
    return ""


def create_empty_notice_data(
    notice_type: Any,
    *,
    include_parser_diagnostics: bool = False,
) -> dict[str, Any]:
    """创建包含该公告类型全部字段的空字典。"""

    normalized_type = normalize_notice_type(notice_type)
    fields = list(get_notice_fields(normalized_type))
    if include_parser_diagnostics:
        fields.extend(PARSER_DIAGNOSTIC_FIELDS.get(normalized_type, ()))
    return {
        field: _empty_value_for_field(field)
        for field in fields
    }


def _is_empty(value: Any) -> bool:
    return value is None or value == "" or value == [] or value == {}


def canonicalize_notice_data(
    notice_type: Any,
    source_data: Mapping[str, Any] | None,
    *,
    include_parser_diagnostics: bool = False,
) -> dict[str, Any]:
    """将网站提取结果转换为固定字段结构。

    只保留对应公告 Schema 已配置的字段；网站解析器产生的其他字段直接忽略。

    兼容规则：
        - 标准字段已有非空值时，旧字段别名不会覆盖标准字段；
        - 对重复的“招标代理机构”，优先保留标准字段“招标代理机构”，
          只有标准字段为空时才使用旧字段“招标代理机构(名称)”补充。
    """

    normalized_type = normalize_notice_type(notice_type)
    fields = list(get_notice_fields(normalized_type))
    if not fields:
        return {}
    if include_parser_diagnostics:
        fields.extend(PARSER_DIAGNOSTIC_FIELDS.get(normalized_type, ()))

    source = dict(source_data or {})
    aliases = {
        **COMMON_FIELD_ALIASES,
        **FIELD_ALIASES.get(normalized_type, {}),
    }

    for old_name, new_name in aliases.items():
        if old_name not in source:
            continue
        if new_name not in source or _is_empty(source.get(new_name)):
            source[new_name] = source[old_name]

    normalized: dict[str, Any] = {}
    for field in fields:
        value = source.get(field, _empty_value_for_field(field))
        if field == "附件":
            value = canonicalize_attachment_list(value)
        elif field in DATETIME_FIELDS:
            value = coerce_datetime(value)
        elif field in DATE_FIELDS:
            value = coerce_date(value)
        elif field in DECIMAL_FIELDS:
            if value in (None, ""):
                value = None
            else:
                amount = coerce_decimal_amount(value)
                # 非人民币金额原文不能安全写入 DECIMAL，暂保留原文等待后续决策。
                value = amount if amount is not None else value
        elif field in STRING_LIST_FIELDS:
            value = _coerce_string_list(value)
        elif field in AMOUNT_LIST_FIELDS:
            value = _coerce_amount_list(
                value,
                preserve_positions=field in {
                    "中标候选人报价",
                    "定标候选人报价",
                    "中标价",
                },
            )
        elif field == "中标候选人明细":
            value = _coerce_candidate_detail_list(value)
        elif field == "中标结果明细":
            value = _coerce_award_detail_list(value)
        elif field in BOOLEAN_FIELDS:
            if isinstance(value, str):
                value = value.strip().lower() in {"1", "true", "yes", "y"}
            else:
                value = bool(value)
        elif value is None:
            value = ""
        normalized[field] = value

    return normalized


def get_missing_fields(
    notice_type: Any,
    data: Mapping[str, Any],
    *,
    include_optional: bool = False,
) -> list[str]:
    """返回空缺字段，用于日志、AI补充任务和质量统计。"""

    normalized_type = normalize_notice_type(notice_type)
    optional = COMMON_OPTIONAL_FIELDS | OPTIONAL_FIELDS.get(
        normalized_type,
        frozenset(),
    )
    missing: list[str] = []

    for field in get_notice_fields(normalized_type):
        if not include_optional and field in optional:
            continue
        if _is_empty(data.get(field)):
            missing.append(field)

    return missing
