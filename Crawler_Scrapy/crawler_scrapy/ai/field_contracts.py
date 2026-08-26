"""八类公告重要业务字段的可执行契约与候选窗口检索。

规则来源：``crawler_scrapy/docs/八类公告重要业务字段提取标准.md``。

该模块不调用模型，只负责：

1. 将文档中的字段定义、标签、AI 策略和窗口类型变成代码契约；
2. 从纯文本中检索少量、可溯源的字段候选窗口；
3. 对项目性质和项目地点执行不依赖 AI 的确定性规范化。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Mapping, Sequence


AI_POLICY_DIRECT = "DIRECT"
AI_POLICY_CONFLICT = "CONFLICT_ONLY"
AI_POLICY_WINDOW = "WINDOW_AI"

WINDOW_LOCAL = "LOCAL"
WINDOW_SECTION = "SECTION"

VALUE_STRING = "string"
VALUE_DATETIME = "datetime"
VALUE_DATE = "date"
VALUE_TIME_RANGE = "time_range_text"
VALUE_RMB_AMOUNT = "rmb_amount"
VALUE_STRING_LIST = "string_list"
VALUE_AMOUNT_LIST = "amount_list"
VALUE_LONG_TEXT = "long_text"


@dataclass(frozen=True)
class BusinessFieldContract:
    name: str
    definition: str
    labels: tuple[str, ...] = ()
    exclusions: tuple[str, ...] = ()
    ai_policy: str = AI_POLICY_CONFLICT
    window_mode: str = WINDOW_LOCAL
    fill_confidence: float = 0.80
    replace_confidence: float = 0.85
    value_type: str = VALUE_STRING
    format_rule: str = "保留原文，不补造、扩写或改变语义。"
    positive_example: str = ""
    negative_example: str = ""

    def prompt_guideline(self) -> str:
        parts = [self.definition]
        if self.exclusions:
            parts.append("禁止包含：" + "、".join(self.exclusions))
        parts.append("没有直接证据时必须省略该字段，不得推测。")
        return "".join(parts)

    def prompt_contract(self) -> dict[str, Any]:
        """返回只包含模型真正需要的信息的紧凑字段契约。"""

        result: dict[str, Any] = {
            "type": self.value_type,
            "definition": self.definition,
            "format": self.format_rule,
        }
        if self.exclusions:
            result["exclude"] = list(self.exclusions)
        if self.positive_example:
            result["positive"] = self.positive_example
        if self.negative_example:
            result["negative"] = self.negative_example
        return result


@dataclass(frozen=True)
class CandidateWindow:
    window_id: str
    start: int
    end: int
    text: str
    fields: tuple[str, ...]
    stage: str
    mode: str


FIELD_LABELS: dict[str, tuple[str, ...]] = {
    "项目名称": ("项目名称", "招标项目名称", "采购项目名称"),
    "项目编号": (
        "招标项目编号", "采购项目编号", "投资项目统一代码",
        "项目编号", "项目代码", "项目编码",
    ),
    "招标编号": ("招标编号", "采购编号", "代理编号"),
    "所属行业": ("所属行业", "行业分类"),
    "组织形式": ("组织形式",),
    "招标方式": ("招标方式", "采购方式"),
    "开标时间": ("开标时间",),
    "项目总投资": ("项目总投资", "总投资额", "投资估算"),
    "项目总投资/估算金额": ("项目总投资", "总投资额", "投资估算", "估算金额"),
    "招标金额": (
        "招标金额", "采购预算", "预算金额", "预算价", "最高投标限价",
        "最高限价", "招标控制价", "本次招标工程费", "本次采购工程费",
    ),
    "资金来源": ("资金来源", "建设资金来源", "建设资金", "资金落实情况"),
    "项目地点": ("项目地点", "建设地点", "实施地点", "服务地点", "交货地点", "供货地点"),
    "建设地点": ("建设地点", "项目地点"),
    "项目规模": (
        "项目规模及内容",
        "建设规模及内容",
        "项目规模",
        "建设规模",
        "工程规模",
    ),
    "建设内容及规模": (
        "建设内容及规模",
        "建设规模及内容",
        "项目规模及内容",
        "建设规模",
        "建设内容",
    ),
    "招标内容": ("招标内容", "招标范围"),
    "项目概况与招标范围": ("项目概况与招标范围", "项目概况", "招标范围"),
    "招标内容与范围": (
        "招标内容与范围",
        "招标内容及范围",
        "招标范围及内容",
        "招标内容",
        "招标范围",
    ),
    "申请人资格要求/投标人资格要求": (
        "申请人的资格要求",
        "投标人的资格要求",
        "申请人资格要求",
        "投标人资格要求",
        "资格要求",
    ),
    "工期/服务期/供货日期": ("工期", "服务期", "供货期", "交货期", "合同履行期限"),
    "质量要求": ("质量要求", "质量标准", "质量目标"),
    "预审文件获取时间": (
        "文件获取时间", "招标文件获取时间", "预审文件获取时间",
        "获取电子招标文件开始时间", "获取时间",
    ),
    "获取方式": ("获取方式", "获取方法", "招标文件获取方式", "获取电子招标文件的方式"),
    "递交截止时间": ("递交截止时间", "递交的截止时间", "投标截止时间"),
    "递交方法": ("递交方法", "递交方式", "递交地址"),
    "开启时间": ("开启时间",),
    "开启方式": ("开启方式", "开标方式"),
    "开启地点": ("开启地点", "开标地点"),
    "评审办法": ("评审办法", "评标办法"),
    "投标保证金方式": ("投标保证金", "保证金递交方式", "保证金方式", "提交保证金的方式"),
    "公示时间": ("公示开始时间", "公示结束时间", "公示时间", "公示期"),
    "中标候选人名称": ("中标候选人", "候选人"),
    "中标候选人报价": ("投标报价", "候选人报价", "报价"),
    "定标候选人名称": ("定标候选人",),
    "定标候选人报价": ("定标候选人报价", "投标报价", "报价"),
    "定标候选人项目经理": ("项目经理", "项目负责人"),
    "定标候选人项目经理相关证书及编号": (
        "项目经理相关证书及编号",
        "项目经理证书",
        "证书名称及编号",
    ),
    "定标候选人项目副经理": ("项目副经理", "副项目经理"),
    "定标候选人项目副经理相关证书及编号": (
        "项目副经理相关证书及编号",
        "项目副经理证书",
    ),
    "定标候选人资信情况": ("资信情况", "企业资信"),
    "定标候选人业绩情况（名称、日期、金额）": (
        "业绩情况",
        "企业业绩",
        "候选人业绩",
    ),
    "中标人名称": ("中标人", "中标单位", "成交供应商"),
    "联合体成员": ("联合体成员", "联合体单位名称", "联合体"),
    "中标价": ("中标价", "中标价格", "中标金额", "成交金额"),
    "工期": ("工期", "服务期", "服务期限", "供货期", "交货期", "合同期限"),
    "项目经理": ("项目经理",),
    "项目经理证书名称": ("证书名称及编号", "相关证书名称及编号", "证书名称", "执业证书"),
    "项目经理证书编号": ("证书名称及编号", "相关证书名称及编号", "证书编号", "注册编号", "注册证号"),
    "依据文件": ("依据文件", "批准文件"),
    "依据文号": ("依据文号", "批准文号"),
    "公告内容": ("更正内容", "变更内容", "公告内容", "终止原因", "废标原因"),
    "合同主要内容": ("合同主要内容", "合同内容", "合同标的"),
    "行政监督部门": ("行政监督部门", "监督部门"),
    "项目类型": ("项目类型", "项目类别"),
    "项目类型/行业分类": ("项目类型", "项目类别", "行业分类"),
    "招标人名称": ("招标人名称", "招标人", "采购人名称", "采购人"),
    "招标人/采购人名称": ("招标人名称", "招标人", "采购人名称", "采购人"),
    "招标人/采购人": ("招标人", "采购人"),
    "招标公告（资格预审公告）预计发布时间": (
        "招标公告预计发布时间",
        "资格预审公告预计发布时间",
        "预计发布时间",
    ),
    "标书发售时间": ("标书发售时间", "招标文件发售时间", "文件获取时间"),
    "公共类型": ("公告类型", "更正类型", "变更类型"),
    "监督部门地址": ("监督部门地址",),
    "监督部门联系人": ("监督部门联系人",),
    "监督部门联系方式": ("监督部门联系方式", "监督电话"),
    "合同名称": ("合同名称",),
    "合同金额": ("合同金额", "签约合同价", "合同总金额"),
    "合同期限": ("合同期限", "履约期限",),
    "合同签署时间": ("合同签署时间", "合同签订时间", "签约日期"),
    "招标人地址": ("招标人地址", "采购人地址"),
    "招标人联系人": ("招标人联系人", "采购人联系人"),
    "招标人联系方式": ("招标人联系方式", "采购人联系方式"),
    "招标代理机构": ("招标代理机构", "代理机构"),
    "招标代理机构地址": ("招标代理机构地址", "代理机构地址"),
    "招标代理机构联系人": ("招标代理机构联系人", "代理机构联系人"),
    "招标代理机构联系方式": ("招标代理机构联系方式", "代理机构联系方式"),
}


LONG_SECTION_FIELDS = frozenset(
    {
        "项目规模",
        "建设内容及规模",
        "招标内容",
        "项目概况与招标范围",
        "招标内容与范围",
        "申请人资格要求/投标人资格要求",
        "质量要求",
        "公告内容",
        "合同主要内容",
        "定标候选人资信情况",
        "定标候选人业绩情况（名称、日期、金额）",
    }
)

WINDOW_AI_FIELDS = frozenset(
    {
        "资金来源",
        *LONG_SECTION_FIELDS,
        "获取方式",
        "递交方法",
        "投标保证金方式",
    }
)

DIRECT_ONLY_FIELDS = frozenset(
    {
        "发布日期",
        "发布网站",
        "项目性质",
        "项目编号/招标编号",
        "招标编号/项目编号",
    }
)


FIELD_DEFINITIONS: dict[str, str] = {
    "项目性质": "只接受源站已验证分类映射出的依法必须招标、非依法招标或其他必须招标。",
    "招标方式": "只取公开招标、邀请招标、询比、询价、竞争性磋商等正文明确方式。",
    "项目名称": "取完整官方项目名称，只删除明确的公告类型后缀，不扩写简称。",
    "项目编号": "只取明确标注为项目编号、招标项目编号、采购项目编号、统一代码或项目代码的完整值。",
    "招标编号": "只取明确标注为招标编号、采购编号或代理编号的完整值。",
    "所属行业": "只取源文明确声明的所属行业或行业分类。",
    "项目类型": "只取源站或正文明确的项目标准分类，不根据项目名称推测。",
    "项目类型/行业分类": "只取源站或正文明确的项目类型或行业分类。",
    "组织形式": "只取委托招标、自行招标等明确组织关系。",
    "开标时间": "只取明确的开标时间；更正内容只取最终有效值。",
    "项目总投资": "取项目整体总投资或投资估算，保留金额单位。",
    "项目总投资/估算金额": "取项目整体总投资或投资估算，保留金额单位。",
    "招标金额": "只取本次招标的预算、最高投标限价或招标控制价。",
    "资金来源": "只取财政资金、企业自筹、银行贷款等资金来源短语。",
    "项目地点": "取项目建设、实施、服务、供货或交货的完整地点原文，可具体到学校、厂区、矿区、园区或道路。",
    "建设地点": "取项目建设所在的完整地点原文，可具体到学校、厂区、矿区、园区或道路。",
    "项目规模": "取项目数量、面积、容量、里程和等级等客观规模，保留原文。",
    "建设内容及规模": "取建设内容和规模完整章节，保留数字和单位，不摘要。",
    "招标内容": "取计划招标的工程、货物或服务内容，保留原文。",
    "项目概况与招标范围": "取项目概况和本次预审范围完整章节，不摘要。",
    "招标内容与范围": "取本次招标包含的工作、货物、服务、标段和边界，不摘要。",
    "申请人资格要求/投标人资格要求": "完整保留资质、人员、业绩、财务、信誉和联合体要求，不摘要。",
    "质量要求": "只取质量、验收或技术标准，保留完整原文。",
    "工期/服务期/供货日期": "只取本次招标明确的工期、服务期、供货期或交货期原文。",
    "工期": "只取中标结果最终确定的工期、服务期、供货期或合同期限。",
    "预审文件获取时间": "取招标或预审文件获取的开始、截止时间，缺少一端时不补造。",
    "递交截止时间": "只取投标或预审申请文件的最终递交截止时间。",
    "开启时间": "只取明确标注的开启时间，不因与开标时间相同而复制。",
    "开启方式": "只取线上、不见面或现场等明确开启方式。",
    "开启地点": "只取明确的开启或开标场所、平台名称，不执行区县截断。",
    "评审办法": "只取公告明确写出的评审或评标办法名称及必要说明，不自行归类。",
    "获取方式": "只取登录、下载、申请、购买或领取文件的实际操作。",
    "递交方法": (
        "只取上传、现场递交、邮寄或送达文件的实际操作；正文明确平台、"
        "线上/线下渠道、文件对象或加密要求时必须一并保留。"
    ),
    "投标保证金方式": "只取现金、保函、保证保险等担保方式。",
    "公示时间": "取公示开始与结束时间；原文只有一端时不补另一端。",
    "中标候选人名称": "按原始排名顺序取全部候选人完整法定名称。",
    "中标候选人报价": "按与候选人相同的顺序提取报价，未公布位置使用 null。",
    "定标候选人名称": "按源文顺序取全部定标候选人完整法定名称，不由模型重排。",
    "定标候选人报价": "按与定标候选人相同的顺序提取报价，未公布位置使用 null。",
    "定标候选人项目经理": "按候选人归属提取项目经理；多候选人时必须保留公司与人员对应关系。",
    "定标候选人项目经理相关证书及编号": "提取与各候选人项目经理对应的证书名称和编号，不取身份证号。",
    "定标候选人项目副经理": "只取公告明确公布的项目副经理，并保留候选人归属。",
    "定标候选人项目副经理相关证书及编号": "只取与项目副经理对应的证书名称和编号。",
    "定标候选人资信情况": "保存公告明确列出的各候选人资信原文，不由模型评价或排名。",
    "定标候选人业绩情况（名称、日期、金额）": "按候选人保留业绩名称、日期、金额的原始对应，不把本次报价当作业绩金额。",
    "中标人名称": "按标段或源文顺序取最终中标人，不取候选人或招标人。",
    "中标价": "按与中标人相同的顺序提取最终中标价。",
    "联合体成员": "只取公告明确列出的联合体成员，不重复已明确的牵头人。",
    "项目经理": "只取中标结果中明确与中标人对应的项目经理。",
    "项目经理证书名称": "只取与中标项目经理对应的证书名称。",
    "项目经理证书编号": "只取与中标项目经理对应的证书或注册编号。",
    "依据文件": "只取明确标注的法律、法规、批准、招标或评标文件名称。",
    "依据文号": "只取明确标注的批准文号或依据文号。",
    "公告内容": "取实际变更事项、变更前后值或终止/废标原因，不摘要。",
    "合同主要内容": "取合同标的、范围和主要履约内容，不摘要。",
    "招标人名称": "只取招标人或采购人的完整法定名称，不附加地址、联系人和电话。",
    "招标人/采购人名称": "只取对应角色块内招标人或采购人的完整法定名称。",
    "招标人/采购人": "只取对应角色块内招标人或采购人的完整法定名称。",
    "招标人地址": "只取招标人或采购人角色块内的完整地址。",
    "招标人联系人": "只取招标人或采购人角色块内联系人姓名，不带职务和电话。",
    "招标人联系方式": "只取招标人或采购人角色块内电话、手机或邮箱，保留脱敏符号。",
    "招标代理机构": "只取代理机构完整法定名称，不附加地址、联系人和电话。",
    "招标代理机构地址": "只取代理机构角色块内的完整地址。",
    "招标代理机构联系人": "只取代理机构角色块内联系人姓名，不带职务和电话。",
    "招标代理机构联系方式": "只取代理机构角色块内电话、手机或邮箱，保留脱敏符号。",
    "行政监督部门": "只取行政监督部门名称，不混入地址、联系人和电话。",
    "招标公告（资格预审公告）预计发布时间": "保留原文公布的日期、月份、季度或时间范围，不虚构精度。",
    "项目编号/招标编号": "兼容字段，由程序根据项目编号和招标编号派生，不由模型提取。",
    "招标编号/项目编号": "兼容字段，由程序根据招标编号和项目编号派生，不由模型提取。",
    "发布日期": "只使用源站 API 或详情元数据，不由模型提取。",
    "发布网站": "只使用站点固定配置，不由模型提取。",
    "公共类型": "只取更正、变更、澄清、延期、终止、流标、废标、撤销或重新招标等明确子类型。",
    "标书发售时间": "只取更正后最终有效的文件发售或获取时间范围。",
    "监督部门地址": "只取监督部门角色块内完整地址。",
    "监督部门联系人": "只取监督部门角色块内联系人姓名。",
    "监督部门联系方式": "只取监督部门角色块内电话、手机或邮箱。",
    "合同名称": "只取正式合同名称，不使用项目名称替代。",
    "合同金额": "只取合同最终人民币总金额，保留原始金额单位。",
    "合同期限": "保留合同或履约期限完整原文，不推算未公布日期。",
    "合同签署时间": "只取合同签署或签订日期。",
}

FIELD_EXCLUSIONS: dict[str, tuple[str, ...]] = {
    "项目名称": ("公告类型后缀", "标段编号", "招标人名称"),
    "项目编号": ("招标编号", "采购编号", "标段编号", "公告ID", "审批文号"),
    "招标编号": ("项目编号", "标段编号", "公告ID", "URL记录ID"),
    "所属行业": ("工程/货物/服务栏目类型", "根据项目名称推测的行业"),
    "资金来源": ("招标人名称", "资金已落实等状态描述"),
    "项目地点": ("招标人地址", "采购人地址", "代理机构地址", "文件递交地址"),
    "建设地点": ("招标人地址", "采购人地址", "代理机构地址", "文件递交地址"),
    "招标金额": ("项目总投资", "中标价", "单价或费率"),
    "项目总投资": ("单个标段金额", "年度预算", "建安费", "招标金额"),
    "项目总投资/估算金额": ("单个标段金额", "年度预算", "建安费", "招标金额"),
    "工期/服务期/供货日期": ("文件获取期", "公示期", "质保期"),
    "工期": ("招标阶段计划工期", "公示期", "质保期"),
    "开启时间": ("递交截止时间", "文件获取时间", "发布日期"),
    "递交截止时间": ("开标时间", "文件获取截止时间", "发布日期"),
    "招标人联系人": ("代理机构联系人", "项目经理", "签章负责人"),
    "招标人联系方式": ("代理机构联系方式", "监督部门联系方式"),
    "招标代理机构联系人": ("招标人联系人", "签章负责人"),
    "招标代理机构联系方式": ("招标人联系方式", "监督部门联系方式"),
    "中标人名称": ("中标候选人", "招标人", "代理机构"),
    "中标价": ("候选人报价", "招标金额", "项目总投资"),
    "获取方式": ("文件售价", "获取时间", "逾期提示"),
    "递交方法": ("逾期拒收条款", "开启地点"),
    "投标保证金方式": ("保证金金额", "缴纳截止时间"),
    "依据文号": ("项目编号", "招标编号", "章节号", "公告ID"),
}


DATETIME_VALUE_FIELDS = frozenset({"发布日期", "开标时间", "递交截止时间", "开启时间"})
DATE_VALUE_FIELDS = frozenset({"合同签署时间"})
TIME_RANGE_VALUE_FIELDS = frozenset(
    {
        "招标公告（资格预审公告）预计发布时间",
        "预审文件获取时间",
        "公示时间",
        "标书发售时间",
        "工期/服务期/供货日期",
        "工期",
        "合同期限",
    }
)
RMB_AMOUNT_FIELDS = frozenset(
    {"项目总投资", "项目总投资/估算金额", "招标金额", "合同金额"}
)
STRING_LIST_VALUE_FIELDS = frozenset(
    {"中标候选人名称", "定标候选人名称", "中标人名称", "联合体成员"}
)
AMOUNT_LIST_VALUE_FIELDS = frozenset({"中标候选人报价", "定标候选人报价", "中标价"})


def _value_type(field_name: str) -> str:
    if field_name in LONG_SECTION_FIELDS:
        return VALUE_LONG_TEXT
    if field_name in DATETIME_VALUE_FIELDS:
        return VALUE_DATETIME
    if field_name in DATE_VALUE_FIELDS:
        return VALUE_DATE
    if field_name in TIME_RANGE_VALUE_FIELDS:
        return VALUE_TIME_RANGE
    if field_name in RMB_AMOUNT_FIELDS:
        return VALUE_RMB_AMOUNT
    if field_name in STRING_LIST_VALUE_FIELDS:
        return VALUE_STRING_LIST
    if field_name in AMOUNT_LIST_VALUE_FIELDS:
        return VALUE_AMOUNT_LIST
    return VALUE_STRING


def _format_rule(field_name: str, value_type: str) -> str:
    if value_type == VALUE_LONG_TEXT:
        return "返回原文章节的起止行，不重写正文；最终值由程序按行切片。"
    if value_type == VALUE_DATETIME:
        return "返回带字段标签证据的原始时间表达；程序再规范为YYYY-MM-DD HH:mm:ss。"
    if value_type == VALUE_DATE:
        return "返回原始日期，必须有年、月、日，不补造时间。"
    if value_type == VALUE_TIME_RANGE:
        return "保留原文精度和起止范围，原文缺少一端时不得补齐。"
    if value_type == VALUE_RMB_AMOUNT:
        return "返回原始人民币总金额及单位；不得提前换算，程序再写入Decimal(18,2)。"
    if value_type == VALUE_STRING_LIST:
        return "返回按原文顺序排列的字符串数组，不合并、不重排。"
    if value_type == VALUE_AMOUNT_LIST:
        return "返回与名称数组同索引的报价数组；未公布位置使用null，保留单位或费率。"
    return "保留原文，不补造、扩写或改变语义。"


FIELD_EXAMPLES: dict[str, tuple[str, str]] = {
    "项目编号": ("项目编号：I14000012340001 -> I14000012340001", "招标编号：QJ-001 不是项目编号"),
    "招标编号": ("招标编号：QJ-001 -> QJ-001", "项目编号：I14000012340001 不是招标编号"),
    "项目地点": ("供货地点：山西省文水中学校 -> 山西省文水中学校", "招标人地址、代理机构地址和文件递交地址不是项目地点"),
    "招标金额": ("最高投标限价：26834.47万元 -> 26834.47万元", "建安费、单价、费率不是招标总金额"),
    "组织形式": ("组织形式：委托招标 -> 委托招标", "招标方式：公开招标 不是组织形式"),
}


def get_field_contract(field_name: str) -> BusinessFieldContract:
    policy = (
        AI_POLICY_DIRECT
        if field_name in DIRECT_ONLY_FIELDS
        else AI_POLICY_WINDOW
        if field_name in WINDOW_AI_FIELDS
        else AI_POLICY_CONFLICT
    )
    value_type = _value_type(field_name)
    positive, negative = FIELD_EXAMPLES.get(field_name, ("", ""))
    return BusinessFieldContract(
        name=field_name,
        definition=FIELD_DEFINITIONS.get(
            field_name,
            f"只提取正文或已验证 API 中能直接支持“{field_name}”的完整内容。",
        ),
        labels=FIELD_LABELS.get(field_name, (field_name,)),
        exclusions=FIELD_EXCLUSIONS.get(field_name, ()),
        ai_policy=policy,
        window_mode=WINDOW_SECTION if field_name in LONG_SECTION_FIELDS else WINDOW_LOCAL,
        value_type=value_type,
        format_rule=_format_rule(field_name, value_type),
        positive_example=positive,
        negative_example=negative,
    )


def normalize_project_nature(value: Any) -> str:
    text = re.sub(r"\s+", "", str(value or ""))
    if not text or text in {"招标信息", "招标项目", "采购项目"}:
        return ""
    if text in {"依法项目", "依法招标", "依法必须招标", "依法必须招标项目"}:
        return "依法必须招标"
    if text in {"非依法招标", "非依法项目", "非招项目"}:
        return "非依法招标"
    if text in {"其他必须招标", "其他必须招标项目"}:
        return "其他必须招标"
    return ""


_LOCATION_NOISE_PREFIX = re.compile(
    r"^(?:(?:项目|工程)(?:所在|位于)?|建设|实施|服务|交货|供货)?地点\s*[\uff1a:]?\s*|"
    r"^(?:项目|工程)\s*(?:位于|坐落于)\s*"
)
def normalize_project_location(value: Any) -> str:
    """保留源文明确给出的完整履约地点，不擅自降级到行政区。

    地点可以是省市区县，也可以是学校、厂区、矿区、道路、园区或源站明确
    写出的“招标人指定地点”。角色错位（例如招标人地址、代理地址、递交
    地址）由证据标签校验负责，本函数只做无损的空白和标签清理。
    """

    text = str(value or "").strip()
    if not text:
        return ""
    text = _LOCATION_NOISE_PREFIX.sub("", text)
    text = text.replace("\u00a0", " ").replace("\u3000", " ")
    text = re.sub(r"[ \t\f\v]+", " ", text)
    text = re.sub(r"\s*\n\s*", "；", text)
    text = re.sub(r"[；;]{2,}", "；", text)
    return text.strip(" ，,、；;.。")


def normalize_contract_value(field_name: str, value: Any) -> Any:
    if field_name == "项目性质":
        return normalize_project_nature(value)
    if field_name in {"项目地点", "建设地点"}:
        return normalize_project_location(value)
    if field_name == "质量要求":
        text = str(value or "").strip()
        if not text:
            return ""
        labels = list(re.finditer(r"(?:质量要求|质量标准|质量目标)\s*[：:]\s*", text))
        if labels:
            text = text[labels[0].end() :]
        text = re.split(
            r"(?:^|\n|。)\s*(?:建设地点|项目地点|服务地点|交货地点|"
            r"招标内容与范围|招标范围|投标人资格要求)\s*[：:]",
            text,
            maxsplit=1,
        )[0]
        return text.strip(" \n；;，,。")
    if field_name in {"项目规模", "建设内容及规模"}:
        text = str(value or "").strip()
        # 明确的相邻字段不得粘进规模。金额和预计发布时间均有独立字段。
        text = re.split(
            r"(?:^|\n|。)\s*(?:项目总投资|总投资额|投资估算|"
            r"招标公告（资格预审公告）预计发布时间)\s*[：:]",
            text,
            maxsplit=1,
        )[0]
        return text.strip(" \n；;，,。")
    return value


def normalize_contract_data(data: Mapping[str, Any]) -> dict[str, Any]:
    result = dict(data)
    for field_name in (
        "项目性质", "项目地点", "建设地点", "质量要求", "项目规模", "建设内容及规模",
    ):
        if field_name in result:
            result[field_name] = normalize_contract_value(
                field_name, result.get(field_name)
            )
    return result


@dataclass(frozen=True)
class _Line:
    start: int
    end: int
    text: str


_HEADING_RE = re.compile(
    r"^(?:(?:第?[\u4e00二三四五六七八九十百]+|第?\d+)\s*[\u7ae0节、.\uff0e）)]|"
    r"\d+(?:\.\d+){0,3}\s+).{0,56}$"
)


def _lines(text: str) -> list[_Line]:
    result: list[_Line] = []
    for match in re.finditer(r"[^\n]+", text):
        value = match.group(0).strip()
        if value:
            left = len(match.group(0)) - len(match.group(0).lstrip())
            right = len(match.group(0).rstrip())
            result.append(_Line(match.start() + left, match.start() + right, value))
    return result


def _is_heading(line: str) -> bool:
    value = line.strip()
    if not value or len(value) > 64:
        return False
    if _HEADING_RE.match(value):
        return True
    bare = value.rstrip("：:")
    labels = {label for values in FIELD_LABELS.values() for label in values}
    return bare in labels


def _line_index_for_offset(lines: Sequence[_Line], offset: int) -> int:
    for index, line in enumerate(lines):
        if line.start <= offset <= line.end:
            return index
    return max(0, len(lines) - 1)


def _anchor_offsets(
    text: str,
    field_name: str,
    rule_value: Any,
) -> list[int]:
    contract = get_field_contract(field_name)
    label_offsets: list[int] = []
    rule_offsets: list[int] = []
    explicit_offsets: list[int] = []
    line_items = _lines(text)
    for label in contract.labels:
        for match in re.finditer(re.escape(label), text):
            label_offsets.append(match.start())
            line_index = _line_index_for_offset(line_items, match.start())
            line = line_items[line_index]
            relative = match.start() - line.start
            prefix = line.text[:relative]
            # “二、项目概况和招标范围”只是宽泛章节标题，不能和
            # “2.3 招标范围：……”这样的真实字段标签同权。只要正文存在
            # 行首字段标签，就优先用它建立窗口，避免扩大复核时选到同章
            # 的项目规模、资格要求等相邻内容。
            stripped_prefix = re.sub(
                r"^\s*(?:(?:[一二三四五六七八九十]+|\d+(?:\.\d+)*)[、.．）)]?\s*)?",
                "",
                prefix,
            )
            suffix = line.text[relative + len(label) :]
            if not stripped_prefix and (
                not suffix or re.match(r"^\s*[：:]", suffix)
            ):
                explicit_offsets.append(match.start())
    if rule_value not in (None, "", [], {}):
        values = rule_value if isinstance(rule_value, (list, tuple)) else [rule_value]
        for item in values:
            raw = str(item or "").strip()
            if not raw:
                continue
            needle = raw[:160]
            rule_offsets.extend(
                match.start() for match in re.finditer(re.escape(needle), text)
            )
    # 有明确标签时不再把包含相同词语的宽泛标题送入模型；规则值锚点仍保留，
    # 便于格式不规范但规则已命中的公告覆盖完整原文行。
    if explicit_offsets:
        # 只有与明确标签处于同一行的规则值才有补充价值。
        explicit_lines = {
            _line_index_for_offset(line_items, offset) for offset in explicit_offsets
        }
        preferred = explicit_offsets + [
            offset
            for offset in rule_offsets
            if _line_index_for_offset(line_items, offset) in explicit_lines
        ]
        return list(dict.fromkeys(sorted(preferred)))
    return list(dict.fromkeys(sorted(label_offsets + rule_offsets)))


def _local_span(lines: Sequence[_Line], index: int, *, expanded: bool) -> tuple[int, int]:
    before = 2 if expanded else 1
    after = 7 if expanded else 3
    start_index = max(0, index - before)
    end_index = min(len(lines) - 1, index + after)
    return lines[start_index].start, lines[end_index].end


_TOP_HEADING_RE = re.compile(r"^(?:第?[一二三四五六七八九十百]+|第?\d+)\s*[、）)]")
_ARABIC_HEADING_RE = re.compile(r"^(\d+(?:\.\d+)*)(?:[、.．）)]|\s+)?\s*(.*)$")


def _heading_signature(line: str) -> tuple[str, tuple[int, ...]] | None:
    value = line.strip()
    if _TOP_HEADING_RE.match(value) and re.match(r"^[一二三四五六七八九十百]+", value):
        return "top", ()
    match = _ARABIC_HEADING_RE.match(value)
    if not match:
        return None
    number = match.group(1)
    # 普通正文以年份或金额开头时不能当作章节编号。
    if len(number) >= 4 and "." not in number:
        return None
    parts = tuple(int(part) for part in number.split("."))
    if len(parts) == 1:
        suffix = value[len(number) : len(number) + 1]
        if suffix and suffix not in "、.．）) \t":
            return None
    return ("decimal" if len(parts) > 1 else "integer"), parts


def _starts_other_field_label(line: str, field_name: str) -> bool:
    value = re.sub(
        r"^\s*(?:(?:[一二三四五六七八九十百]+|\d+(?:\.\d+)*)[、.．）)]?\s*)?",
        "",
        line,
    )
    current_labels = set(get_field_contract(field_name).labels)
    for other_field, labels in FIELD_LABELS.items():
        if other_field == field_name:
            continue
        for label in labels:
            if label in current_labels:
                continue
            if re.match(rf"^{re.escape(label)}\s*[：:]", value):
                return True
    return False


def _section_span(
    lines: Sequence[_Line],
    index: int,
    *,
    expanded: bool,
    field_name: str,
) -> tuple[int, int]:
    start_index = index
    # 标签位于章节内部时，向上找最近的章节标题。
    for candidate in range(index, max(-1, index - (8 if expanded else 3)), -1):
        if _is_heading(lines[candidate].text):
            start_index = candidate
            break
    anchor_signature = _heading_signature(lines[index].text)
    end_index = len(lines) - 1
    for candidate in range(index + 1, len(lines)):
        candidate_text = lines[candidate].text
        signature = _heading_signature(candidate_text)
        is_boundary = bool(signature and signature[0] == "top")
        if anchor_signature and signature and not is_boundary:
            anchor_kind, anchor_parts = anchor_signature
            candidate_kind, candidate_parts = signature
            if anchor_kind == "decimal" and candidate_kind == "decimal":
                is_boundary = len(candidate_parts) <= len(anchor_parts)
            elif anchor_kind == "integer" and candidate_kind == "integer":
                is_boundary = True
            # 顶层中文章节内的 3.1、1、 等都是子条款，只遇下一个
            # “四、……”才结束，不能在第一条资格要求处截断。
        if not is_boundary and _starts_other_field_label(candidate_text, field_name):
            is_boundary = True
        if is_boundary:
            end_index = candidate - 1
            break
    # 防止格式损坏的公告在没有下一标题时把全文送给 AI。
    start = lines[start_index].start
    hard_limit = 4200 if expanded else 2800
    end = min(lines[end_index].end, start + hard_limit)
    return start, end


def build_candidate_windows(
    text: str,
    fields: Sequence[str],
    rule_data: Mapping[str, Any] | None = None,
    *,
    stage: str = "candidate",
    max_per_field: int = 2,
) -> list[CandidateWindow]:
    """为字段建立带原文偏移的少量候选窗口。

    ``stage='candidate'`` 对短字段使用标签邻域，对长字段直接使用
    完整章节；``stage='expanded'`` 仅用于冲突、低置信或证据失败字段。
    """

    source = str(text or "")
    line_items = _lines(source)
    if not source or not line_items:
        return []
    expanded = stage == "expanded"
    rule_values = dict(rule_data or {})
    raw_windows: list[tuple[int, int, str, str]] = []
    for field_name in dict.fromkeys(str(field) for field in fields if str(field)):
        contract = get_field_contract(field_name)
        if contract.ai_policy == AI_POLICY_DIRECT:
            continue
        anchors = _anchor_offsets(source, field_name, rule_values.get(field_name))
        limit = max(1, max_per_field)
        if len(anchors) > limit and limit > 1:
            # 变更公告往往先写原值、文末再写最终值；
            # 首尾均保留比只取第一次命中更安全。
            selected_anchors = [anchors[0], anchors[-1]]
        else:
            selected_anchors = anchors[:limit]
        for offset in selected_anchors:
            index = _line_index_for_offset(line_items, offset)
            effective_mode = (
                WINDOW_SECTION
                if expanded or contract.window_mode == WINDOW_SECTION
                else WINDOW_LOCAL
            )
            if effective_mode == WINDOW_SECTION:
                start, end = _section_span(
                    line_items,
                    index,
                    expanded=expanded,
                    field_name=field_name,
                )
            else:
                start, end = _local_span(line_items, index, expanded=expanded)
            raw_windows.append((start, end, field_name, effective_mode))

    # 相同或大部分重叠的窗口合并，避免提示词重复发送原文。
    grouped: list[dict[str, Any]] = []
    for start, end, field_name, mode in sorted(raw_windows):
        target = None
        for current in grouped:
            overlap = max(0, min(end, current["end"]) - max(start, current["start"]))
            shortest = max(1, min(end - start, current["end"] - current["start"]))
            if (start == current["start"] and end == current["end"]) or overlap / shortest >= 0.8:
                target = current
                break
        if target is None:
            grouped.append(
                {"start": start, "end": end, "fields": [field_name], "modes": [mode]}
            )
        else:
            target["start"] = min(target["start"], start)
            target["end"] = max(target["end"], end)
            if field_name not in target["fields"]:
                target["fields"].append(field_name)
            target["modes"].append(mode)

    result: list[CandidateWindow] = []
    prefix = "E" if expanded else "C"
    for index, current in enumerate(grouped, 1):
        start, end = current["start"], current["end"]
        result.append(
            CandidateWindow(
                window_id=f"{prefix}{index:03d}",
                start=start,
                end=end,
                text=source[start:end].strip(),
                fields=tuple(current["fields"]),
                stage=stage,
                mode=(
                    WINDOW_SECTION
                    if WINDOW_SECTION in current["modes"]
                    else WINDOW_LOCAL
                ),
            )
        )
    return result


def windows_by_field(
    windows: Sequence[CandidateWindow],
) -> dict[str, list[CandidateWindow]]:
    result: dict[str, list[CandidateWindow]] = {}
    for window in windows:
        for field_name in window.fields:
            result.setdefault(field_name, []).append(window)
    return result


__all__ = [
    "AI_POLICY_CONFLICT",
    "AI_POLICY_DIRECT",
    "AI_POLICY_WINDOW",
    "BusinessFieldContract",
    "CandidateWindow",
    "DIRECT_ONLY_FIELDS",
    "AMOUNT_LIST_VALUE_FIELDS",
    "DATETIME_VALUE_FIELDS",
    "DATE_VALUE_FIELDS",
    "FIELD_LABELS",
    "LONG_SECTION_FIELDS",
    "RMB_AMOUNT_FIELDS",
    "STRING_LIST_VALUE_FIELDS",
    "TIME_RANGE_VALUE_FIELDS",
    "VALUE_AMOUNT_LIST",
    "VALUE_LONG_TEXT",
    "VALUE_RMB_AMOUNT",
    "VALUE_STRING_LIST",
    "WINDOW_AI_FIELDS",
    "build_candidate_windows",
    "get_field_contract",
    "normalize_contract_data",
    "normalize_contract_value",
    "normalize_project_location",
    "normalize_project_nature",
    "windows_by_field",
]
