"""山西交控服务端 HTML 列表、详情和统一业务字段解析。"""

from __future__ import annotations

import mimetypes
import re
from dataclasses import dataclass
from html import unescape
from typing import Any, Mapping

from lxml import etree, html as lxml_html

from crawler_scrapy.sites.bitbid.parser import BitbidParser, clean_html
from crawler_scrapy.sites.sxjkzcpt import config


@dataclass(frozen=True)
class ListRecord:
    notice_id: str
    title: str
    publish_time: str


@dataclass
class ParsedNotice:
    category: str
    notice_type: str
    title: str
    publish_time: str
    raw_text: str
    data: dict[str, Any]
    attachments: list[dict[str, Any]]
    structured: dict[str, str]
    access: dict[str, Any]


def _space(value: Any) -> str:
    text = unescape(str(value or "")).replace("\xa0", " ").replace("\u3000", " ")
    return re.sub(r"\s+", " ", text).strip()


def _document(value: bytes | str):
    source = (
        value.decode("utf-8", errors="replace")
        if isinstance(value, bytes)
        else str(value or "")
    )
    return lxml_html.fromstring(source or "<html></html>")


def parse_list_records(value: bytes | str) -> list[ListRecord]:
    """解析列表 HTML 片段，不依赖页面脚本执行。"""

    root = _document(value)
    result: list[ListRecord] = []
    for block in root.cssselect(".erjizt-right-cont-dt"):
        anchors = block.cssselect("a[onclick*='toDetail']")
        if not anchors:
            continue
        anchor = anchors[0]
        onclick = str(anchor.get("onclick") or "")
        matched = re.search(r"toDetail\(['\"]([^'\"]+)['\"]\)", onclick)
        if not matched:
            continue
        title_nodes = block.cssselect(".dt-textb")
        title = (
            _space(title_nodes[0].get("title") or "".join(title_nodes[0].itertext()))
            if title_nodes
            else _space(anchor.get("title") or "".join(anchor.itertext()))
        )
        date_nodes = block.cssselect(".dt-texte")
        publish_time = _space("".join(date_nodes[-1].itertext())) if date_nodes else ""
        result.append(ListRecord(matched.group(1), title, publish_time))
    return result


def parse_total_pages(value: bytes | str) -> int:
    source = value.decode("utf-8", errors="replace") if isinstance(value, bytes) else str(value or "")
    matched = re.search(r"var\s+totalPage\s*=\s*(\d+)", source)
    return int(matched.group(1)) if matched else 0


def extract_csrf(value: bytes | str) -> str:
    root = _document(value)
    values = root.cssselect("input[name='_csrf']")
    return str(values[0].get("value") or "").strip() if values else ""


def classify_category(source_category: str, title: str) -> tuple[str, str]:
    """按标题纠正源站混栏，同时保留 Schema 兼容的中文公告类型。"""

    compact = re.sub(r"\s+", "", str(title or ""))
    if source_category == "plan" or "采购计划" in compact or "招标计划" in compact:
        return "plan", "招标计划"
    if source_category == "contract" or any(x in compact for x in ("合同订立", "合同公告", "合同履约")):
        return "contract", "合同与履约"
    if any(x in compact for x in ("流标", "废标", "终止", "撤销")):
        # 公共 Schema 没有终止字段形状，复用招标公告字段，导出时改数据库编码。
        return "termination", "招标公告"
    if "更正" in compact and any(x in compact for x in ("候选人", "结果", "中标", "成交")):
        return "correction", "更正结果公示"
    if any(x in compact for x in ("中标候选人", "成交候选人", "承租候选人", "竞租候选人")):
        return "candidate", "中标候选人公示"
    if any(x in compact for x in ("中标结果", "成交结果", "结果公告", "中标公告", "成交公告")):
        return "award", "中标结果公示"
    if source_category == "change" or any(x in compact for x in ("变更", "延期", "澄清", "答疑")):
        return "change", "招标公告"
    return "tender", "招标公告"


class SxjkzcptParser(BitbidParser):
    parser_version = "sxjkzcpt-v5-random-field-audit"

    @classmethod
    def parse(
        cls,
        source_feed: str,
        value: bytes | str,
        *,
        list_record: Mapping[str, Any] | None = None,
    ) -> ParsedNotice:
        root = _document(value)
        source_category = source_feed.split(".", 1)[1]
        channel = source_feed.split(".", 1)[0]
        title_nodes = root.cssselect("h1.firth-tit")
        title = _space("".join(title_nodes[0].itertext())) if title_nodes else _space((list_record or {}).get("title"))
        source = value.decode("utf-8", errors="replace") if isinstance(value, bytes) else str(value or "")
        restricted = bool(re.search(r"var\s+isNbgg\s*=\s*true", source, re.I))
        content_nodes = root.cssselect("#content")
        content_html = etree.tostring(content_nodes[0], encoding="unicode", method="html") if content_nodes else ""
        raw_text = clean_html(content_html)
        category, notice_type = classify_category(source_category, title)
        # 旧候选人公示的顶部标题偶尔被截断，仅当标题未能分类且
        # 来源是候选/结果栏时，才用正文开头的明确“候选人公示”补判。
        # 不对已识别的终止公告重分类，避免正文“招标计划有变”误判为计划。
        if category == "tender" and source_category in {"candidate", "award"}:
            prefix = re.sub(r"\s+", "", raw_text[:300])
            if any(
                marker in prefix
                for marker in ("中标候选人公示", "成交候选人公示", "承租候选人公示", "竞租候选人公示")
            ):
                category, notice_type = "candidate", "中标候选人公示"
        structured = cls._structured_fields(root)
        publish_time = cls._publish_time(root) or _space((list_record or {}).get("publish_time"))
        page_text = _space("".join(root.itertext()))
        login_prompt = bool(re.search(r"(?:请)?登录\s*系统查看", page_text))
        ca_prompt = "插入企业CA锁方可查看" in page_text
        access = {
            "isNbgg": restricted,
            # 公开详情模板也内置一段 display:none 的 CA 提示；
            # 只有正文空且提示存在时才表示当前详情真正受限。
            "requiresLogin": restricted or (not raw_text and (login_prompt or ca_prompt)),
            "requiresCa": restricted or (not raw_text and ca_prompt),
            "publicContentPresent": bool(raw_text),
        }
        if restricted or not raw_text:
            return ParsedNotice(category, notice_type, title, publish_time, raw_text, {}, [], structured, access)

        text = raw_text
        project_number = cls._clean_identifier(cls._project_identifier(text) or cls._header_exact(
            structured, "投资项目统一代码", "项目代码", "项目编号"
        ))
        # Schema 中的“招标编号”应对应正文明确标注的业务编号。
        # 顶部“交控集团招采认证编号”仅在正文未公开编号时兜底，
        # 原值仍完整保留在 _trace.payload.detailStructured 中便于溯源。
        tender_number = cls._clean_identifier(
            cls._identifier_label(text, "招标编号", "采购编号", "代理编号")
            or cls._header_exact(structured, "招标编号", "采购编号")
            or cls._identifier_label(text, "交控集团招采认证编号")
            or cls._header_exact(structured, "交控集团招采认证编号")
        )
        contacts = cls._clean_contacts(cls._contacts(text))
        source_nature = cls._source_nature(category, title, channel)

        if category == "plan":
            data = cls._plan(structured, text)
        elif category in {"tender", "change", "termination"}:
            data = cls._tender(
                structured, text, contacts, channel, source_nature, category
            )
        elif category == "candidate":
            data = cls._candidate(root, structured, text, contacts, channel, source_nature)
        elif category == "award":
            data = cls._award(root, structured, text, contacts, channel, source_nature)
        elif category == "correction":
            data = cls._correction(structured, text, contacts, channel, source_nature)
        elif category == "contract":
            data = cls._contract(structured, text)
        else:
            raise ValueError(f"未知山西交控公告类别：{category}")

        data["项目编号"] = project_number
        data["招标编号"] = tender_number
        # 详情页时间精确到秒；统一覆盖各 Schema 构造器中的占位值。
        if "发布日期" in data:
            data["发布日期"] = publish_time
        combined = "；".join(dict.fromkeys(filter(None, (project_number, tender_number))))
        for key in ("项目编号/招标编号", "招标编号/项目编号"):
            if key in data:
                data[key] = combined
        return ParsedNotice(
            category,
            notice_type,
            title,
            publish_time,
            text,
            data,
            cls._attachments(root),
            structured,
            access,
        )

    @staticmethod
    def _structured_fields(root) -> dict[str, str]:
        result: dict[str, str] = {}
        for table in root.cssselect("#contentBody, #content table"):
            for row in table.xpath(".//tr"):
                cells = [_space("".join(cell.itertext())) for cell in row.xpath("./th|./td")]
                index = 0
                while index + 1 < len(cells):
                    label = cells[index].rstrip("：:").strip()
                    if label and (cells[index].endswith(("：", ":")) or len(label) <= 32):
                        value = cells[index + 1].strip()
                        if value and label not in result:
                            result[label] = value
                        index += 2
                    else:
                        index += 1
        return result

    @staticmethod
    def _publish_time(root) -> str:
        nodes = root.cssselect("p.remark")
        if not nodes:
            return ""
        text = _space("".join(nodes[0].itertext()))
        matched = re.search(r"发布时间[：:]\s*(\d{4})[/-](\d{1,2})[/-](\d{1,2})\s+(\d{1,2}:\d{2}:\d{2})", text)
        if not matched:
            return ""
        return f"{matched.group(1)}-{int(matched.group(2)):02d}-{int(matched.group(3)):02d} {matched.group(4)}"

    @staticmethod
    def _header(values: Mapping[str, str], *names: str) -> str:
        for name in names:
            for key, value in values.items():
                if key == name or name in key:
                    return str(value or "").strip()
        return ""

    @staticmethod
    def _header_exact(values: Mapping[str, str], *names: str) -> str:
        normalized = {str(key).rstrip("：:").strip(): value for key, value in values.items()}
        for name in names:
            value = str(normalized.get(name) or "").strip()
            if value:
                return value
        return ""

    @staticmethod
    def _project_identifier(text: str) -> str:
        """提取项目侧编号，与顶部交控招采认证编号明确分列。"""

        patterns = (
            r"投资项目统一代码\s*[：:]\s*([^\n，,。；;]+)",
            r"招标项目编号\s*[：:]\s*([^\n，,。；;]+)",
            r"采购项目编号\s*[：:]\s*([^\n，,。；;]+)",
            r"项目代码\s*[：:]\s*([^\n，,。；;]+)",
            r"(?<!招标)(?<!采购)项目编号\s*[：:]\s*([^\n，,。；;]+)",
        )
        for pattern in patterns:
            matched = re.search(pattern, text)
            if matched:
                return matched.group(1).strip().split()[0]
        return ""

    @staticmethod
    def _clean_identifier(value: str) -> str:
        text = str(value or "").strip(" ：:（）()")
        chinese_style = re.match(
            r"[A-Za-z0-9][A-Za-z0-9._\-招采字第号【】〔〕\[\]]{3,}", text
        )
        if chinese_style and any(char in chinese_style.group(0) for char in "【〔["):
            return chinese_style.group(0).rstrip("，,。；;")
        # 编号之后常直接跟“已由……批准建设”，只保留可验证的代码部分。
        matched = re.match(r"[A-Za-z0-9][A-Za-z0-9._-]{3,}", text)
        return matched.group(0) if matched else text.split()[0] if text else ""

    @staticmethod
    def _clean_contacts(
        contacts: Mapping[str, Mapping[str, str]],
    ) -> dict[str, dict[str, str]]:
        result = {key: dict(value) for key, value in contacts.items()}
        for value in result.values():
            name = str(value.get("name") or "")
            value["name"] = re.sub(
                r"\s*[（(](?:电话|联系人|地址|联系方式)[：:].*?[）)]\s*$", "", name
            ).strip(" 。；;")
        return result

    @staticmethod
    def _funding_source(text: str) -> str:
        # 工程建设模板常写“建设资金来自企业自筹（资金来源）”。如果先匹配
        # 通用“资金来源”标签，会把括号后的“），出资比例……”误当成值。
        construction = re.search(
            r"(?:项目)?建设资金来自\s*(.+?)"
            r"(?=\s*[（(]资金来源[）)]|，\s*出资比例|"
            r"，\s*(?:招标人|采购人|建设单位)\s*为|[\n。；;]|$)",
            text,
        )
        if construction:
            return construction.group(1).strip(" ：:")
        matched = re.search(
            r"(?:项目)?资金来源(?:为|是|由)?\s*[：:]?\s*"
            r"(.+?)(?=，\s*(?:招标人|采购人|建设单位)\s*为|[\n。；;]|$)",
            text,
        )
        if matched:
            return matched.group(1).strip(" ：:")
        capital = re.search(
            r"项目资本金来源\s*(.+?)(?=[\n。；;]|$)", text
        )
        return capital.group(1).strip(" ：:") if capital else ""

    @classmethod
    def _contacts(cls, text: str) -> dict[str, dict[str, str]]:
        """优先解析公告末尾的正式“联系方式”章节。

        避免把异议、监督或履约保证金账户中的联系人错当成招标人
        联系人；同时兼容代理机构使用“项目负责人”的模板。
        """

        headings = list(re.finditer(
            r"(?m)^\s*(?:(?:\d+(?:\.\d+)*)|[\u4e00-\u9fa5]{1,3})?\s*[.、]?\s*"
            r"联系方式\s*[：:]?\s*$",
            text,
        ))
        contact_text = text[headings[-1].end():] if headings else text
        result = {"owner": {}, "agency": {}}
        owner_label = r"(?:招\s*标\s*人|采\s*购\s*人|招标单位|采购单位|建设单位|委托人)"
        agency_label = r"(?:招标代理机构(?:名称)?|采购代理机构|招标代理|产权交易机构)"
        owner = re.search(
            rf"(?s){owner_label}\s*[：:]\s*(.*?)(?={agency_label}\s*[：:]|\Z)",
            contact_text,
        )
        agency = re.search(
            rf"(?s){agency_label}\s*[：:]\s*(.*?)(?=招标人或其招标代理机构|\Z)",
            contact_text,
        )
        for key, match in (("owner", owner), ("agency", agency)):
            if not match:
                continue
            block = match.group(1).strip()
            first = block.splitlines()[0].strip(" 。") if block else ""
            result[key] = {
                "name": first,
                "address": cls._label(block, "地址", "联系地址"),
                "contact": cls._label(
                    block, "项目联系人", "联系人", "联 系 人", "项目负责人"
                ),
                "phone": cls._label(
                    block, "电话", "联系电话", "联系方式", "联 系 方 式",
                    "联络电话", "传真电话", "电话/传真",
                ),
            }
        # 部分公示只在正文开头写“招标人：公司（电话：...）”，
        # 末尾只重复单位名；仅用同一单位括号内的明示号码补空。
        for key, label in (("owner", owner_label), ("agency", agency_label)):
            if result[key].get("phone"):
                continue
            embedded = re.search(
                rf"{label}\s*[：:]\s*(.+?)\s*[（(]\s*"
                rf"(?:电话|联系方式)\s*[：:]?\s*([0-9][0-9\-\s、/]+)[）)]",
                text,
            )
            if not embedded:
                continue
            if not result[key].get("name"):
                result[key]["name"] = embedded.group(1).strip()
            result[key]["phone"] = embedded.group(2).strip()
        return result

    @staticmethod
    def _source_nature(category: str, title: str, channel: str) -> str:
        mapping = {
            "plan": "采购计划",
            "tender": "招标公告",
            "candidate": "中标候选人公示",
            "award": "中标结果公示",
            "contract": "合同订立信息",
            "correction": "更正公告",
            "termination": "终止/废标/流标公告",
        }
        if category == "change":
            nature = next((x for x in ("延期", "澄清", "答疑", "变更") if x in title), "变更")
            return f"{nature}公告（{channel.upper()}）"
        return f"{mapping.get(category, category)}（{channel.upper()}）"

    @classmethod
    def _plan(cls, h: Mapping[str, str], text: str) -> dict[str, Any]:
        return {
            "项目性质": cls._header(h, "发布类型") or "自主发布",
            "招标方式": cls._header(h, "招标方式"),
            "项目名称": cls._header(h, "项目名称") or cls._project_name_from_text(text),
            "项目类型": cls._header(h, "项目类型"),
            "项目总投资": cls._header(h, "项目总投资", "投资估算"),
            "招标内容": cls._header(h, "招标内容"),
            "招标人名称": cls._header(h, "招标人名称", "发布单位"),
            "行政监督部门": cls._header(h, "行政监督部门"),
            "建设地点": cls._header(h, "建设地点"),
            "建设内容及规模": cls._header(h, "建设内容及规模"),
            "招标公告（资格预审公告）预计发布时间": cls._header(h, "预计发布时间"),
            "发布日期": "",
            "发布网站": config.PLATFORM_NAME,
        }

    @classmethod
    def _tender(
        cls,
        h: Mapping[str, str],
        text: str,
        contacts: Mapping[str, Mapping[str, str]],
        channel: str,
        source_nature: str,
        category: str,
    ) -> dict[str, Any]:
        body_open_time = cls._last_label(
            text, "开标时间", "开启时间", "竞价开始时间"
        ) or cls._narrative_time(text, "竞价开始时间")
        if category in {"change", "termination"}:
            # 源站部分变更页的顶部“变更开标时间”仍错误填写旧值；
            # 正文“现变更为/现延期为”之后最后一个具体时间才是最终值。
            concrete_body_time = (
                body_open_time if re.search(r"\d{4}\s*(?:年|[-/])", body_open_time) else ""
            )
            header_open_time = cls._header_exact(
                h, "变更开标时间", "开标时间"
            )
            last_deadline = cls._deadline(text)
            header_conflicts_with_deadline = bool(
                last_deadline
                and re.search(r"\d{4}\s*(?:年|[-/])", last_deadline)
                and cls._time_identity(header_open_time)
                and cls._time_identity(header_open_time)
                != cls._time_identity(last_deadline)
            )
            open_time = concrete_body_time
            if category == "change" and not open_time and not header_conflicts_with_deadline:
                open_time = header_open_time or body_open_time
        else:
            open_time = (
                cls._header_exact(h, "变更开标时间", "开标时间")
                or body_open_time
            )
        start = cls._header(h, "招标文件获取开始时间", "文件获取开始时间")
        end = cls._header(h, "变更招标文件获取截止时间", "招标文件获取截止时间", "文件获取截止时间")
        return {
            "项目性质": "依法必须招标" if channel == "zbcg" else "其他必须招标",
            "源站公告性质": source_nature,
            "项目名称": cls._header(h, "项目名称") or cls._project_name_from_text(text),
            "所属行业": cls._label(text, "所属行业"),
            "组织形式": "委托招标" if contacts.get("agency", {}).get("name") else "",
            "开标时间": open_time,
            "项目编号/招标编号": "",
            "项目类型/行业分类": cls._header(h, "招采类型", "项目类型"),
            "项目总投资/估算金额": cls._label(text, "项目总投资", "估算金额", "投资估算"),
            "招标金额": cls._tender_amount(text),
            "资金来源": cls._funding_source(text),
            "项目地点": cls._clean_prose(cls._label(text, "招标项目所在地区", "项目所在地", "项目地点", "建设地点", "建设地址", "供货/服务地点", "服务地点", "交货地点")),
            "招标人/采购人名称": contacts.get("owner", {}).get("name", ""),
            "项目规模": cls._label_or_block(text, "项目规模", "建设规模", "项目概况"),
            "工期/服务期/供货日期": cls._label_or_block(
                text, "合同履行期限", "计划工期", "监理服务期限",
                "监理周期", "设计服务期限", "服务周期", "供货期/服务期", "服务期", "服务期限",
                "交货期", "供货期", "工期",
            ),
            "质量要求": cls._label_or_block(
                text, "服务成果要求（质量目标承诺）", "服务成果要求(质量目标承诺)",
                "质量标准/服务要求", "质量/技术标准", "质量要求", "质量标准"
            ),
            "招标内容与范围": cls._section(text, ("项目概况和招标范围", "项目概况与招标范围", "招标内容与范围", "采购内容与范围", "招标范围", "采购范围"), ("投标人资格要求", "供应商资格要求", "招标文件的获取", "采购文件的获取")),
            "申请人资格要求/投标人资格要求": cls._section(text, ("投标人资格要求", "申请人资格要求", "供应商资格要求"), ("招标文件的获取", "采购文件的获取", "投标文件的递交", "响应文件的递交")),
            "预审文件获取时间": cls._range(start, end) or cls._last_label(text, "获取时间", "招标文件获取时间"),
            "获取方式": cls._last_label(text, "获取方法", "获取方式"),
            "递交截止时间": cls._deadline(text),
            "递交方法": cls._last_label(text, "递交方法", "递交方式"),
            "开启时间": open_time,
            "开启方式": cls._last_label(text, "开标方式", "开启方式"),
            "开启地点": cls._last_label(text, "开标地点", "开启地点", "竞价地址"),
            "评审办法": cls._evaluation_method(text),
            "投标保证金方式": cls._guarantee(text),
            **cls._contact_fields(contacts, award=False),
            "发布日期": "",
            "发布网站": config.PLATFORM_NAME,
        }

    @classmethod
    def _candidate(cls, root, h, text, contacts, channel, source_nature):
        table_details = cls._candidate_table(root)
        text_details = cls._candidate_text(text)
        # 有些旧公告后半段的“单位业绩表”只有候选人名称、没有报价；正文
        # 开头却在同一行明确披露报价。此时正文结果的信息更完整。
        details = (
            text_details
            if text_details and not any(row.get("候选人报价") for row in table_details)
            else table_details or text_details
        )
        return {
            "项目性质": "依法必须招标" if channel == "zbcg" else "其他必须招标",
            "源站公告性质": source_nature,
            "项目名称": cls._header(h, "项目名称") or cls._project_name_from_text(text),
            "所属行业": cls._header(h, "招采类型"),
            "组织形式": "委托招标" if contacts.get("agency", {}).get("name") else "",
            "开标时间": cls._header(h, "开标时间"),
            "公示时间": cls._range(cls._header(h, "公示开始时间"), cls._header(h, "公示结束时间")) or cls._publicity_time(text),
            "招标编号/项目编号": "",
            "中标候选人名称": [row["候选人名称"] for row in details],
            # 保留空报价占位，确保与候选人名称按索引一一对应。
            "中标候选人报价": [row.get("候选人报价", "") for row in details],
            "中标候选人明细": details,
            **cls._contact_fields(contacts, award=True),
            "发布日期": "",
            "发布网站": config.PLATFORM_NAME,
        }

    @classmethod
    def _award(cls, root, h, text, contacts, channel, source_nature):
        name = cls._header(h, "成交供应商名称", "中标人名称", "中标供应商名称")
        price = cls._header(h, "中标金额", "成交金额")
        amount_key = next((key for key in h if key.startswith(("中标金额", "成交金额"))), "")
        if price and "万元" in amount_key and not re.search(r"元|万元", price):
            price = f"{price}万元"
        price = cls._clean_award_price(price, text)
        name, consortium = cls._award_parties(text, name)
        details = ([{"标段": "", "中标人名称": name, "中标价": price}] if name else cls._award_text(text))
        return {
            "项目性质": "依法必须招标" if channel == "zbcg" else "其他必须招标",
            "源站公告性质": source_nature,
            "项目名称": cls._header(h, "项目名称") or cls._project_name_from_text(text),
            "所属行业": cls._header(h, "招采类型"),
            "组织形式": "委托招标" if contacts.get("agency", {}).get("name") else "",
            "招标方式": cls._header(h, "采购方式") or cls._label(text, "招标方式"),
            "中标人名称": [row["中标人名称"] for row in details],
            "联合体成员": consortium,
            "中标价": [row.get("中标价", "") for row in details],
            "中标结果明细": details,
            "工期": cls._label(text, "合同履行期限", "服务期限", "工期", "服务期", "交货期"),
            "项目经理": cls._label(text, "项目经理", "项目负责人"),
            "项目经理证书名称": cls._label(text, "证书名称"),
            "项目经理证书编号": cls._label(text, "证书编号"),
            **cls._contact_fields(contacts, award=True),
            "依据文件": cls._label(text, "依据文件"),
            "依据文号": cls._label(text, "依据文号"),
            "发布日期": "",
            "发布网站": config.PLATFORM_NAME,
        }

    @classmethod
    def _correction(cls, h, text, contacts, channel, source_nature):
        return {
            "公共类型": source_nature,
            "项目名称": cls._header(h, "项目名称") or cls._project_name({}, text),
            "所属行业": cls._header(h, "招采类型"),
            "组织形式": "委托招标" if contacts.get("agency", {}).get("name") else "",
            "开标时间": cls._header(h, "开标时间"),
            "标书发售时间": cls._range(cls._header(h, "招标文件获取开始时间"), cls._header(h, "招标文件获取截止时间")),
            "公告内容": text,
            **cls._contact_fields(contacts, award=False),
            "监督部门地址": cls._label(text, "监督部门地址"),
            "监督部门联系人": cls._label(text, "监督部门联系人"),
            "监督部门联系方式": cls._label(text, "监督部门联系方式"),
            "依据文件": cls._label(text, "依据文件"),
            "依据文号": cls._label(text, "依据文号"),
            "发布日期": "",
            "发布网站": config.PLATFORM_NAME,
        }

    @classmethod
    def _contract(cls, h, text):
        return {
            "项目名称": cls._header(h, "项目名称") or cls._project_name_from_text(text),
            "合同名称": cls._header(h, "合同名称") or cls._label(text, "合同名称"),
            "招标人名称": cls._header(h, "招标人名称", "采购人名称") or cls._label(text, "招标人", "采购人"),
            "中标人名称": cls._header(h, "中标人名称", "供应商名称") or cls._label(text, "中标人", "供应商"),
            "合同金额": cls._header(h, "合同金额") or cls._label(text, "合同金额"),
            "合同期限": cls._header(h, "合同期限") or cls._label(text, "合同期限"),
            "合同签署时间": cls._header(h, "合同签署时间") or cls._label(text, "合同签署时间"),
            "合同主要内容": cls._section(text, ("合同主要内容",), ("其他", "发布日期")),
            "发布日期": "",
            "发布网站": config.PLATFORM_NAME,
        }

    @staticmethod
    def _last_label(text: str, *labels: str) -> str:
        values: list[tuple[int, str]] = []
        for label in labels:
            for match in re.finditer(rf"{re.escape(label)}\s*[：:]\s*([^\n；;]+)", text):
                values.append((match.start(), match.group(1).strip()))
        return max(values, default=(-1, ""), key=lambda item: item[0])[1]

    @classmethod
    def _label_or_block(cls, text: str, *labels: str) -> str:
        """兼容“2.1 字段：值”和“2.1 字段\n多行值”两种模板。"""

        direct = cls._label(text, *labels)
        if direct:
            return direct
        for label in labels:
            matched = re.search(
                rf"(?ms)^\s*\d+(?:\.\d+)+\s*{re.escape(label)}\s*\n"
                rf"(.*?)(?=^\s*(?:\d+(?:\.\d+)+|\d+[.、]|"
                rf"[一二三四五六七八九十]+[.、])\s*\S|\Z)",
                text,
            )
            if matched:
                return matched.group(1).strip()
        return ""

    @staticmethod
    def _clean_prose(value: str) -> str:
        return re.sub(r"\s+([，。；;])", r"\1", str(value or "")).strip()

    @classmethod
    def _project_name_from_text(cls, text: str) -> str:
        """从正文的明确标签、项目编号引导句或首行公告标题还原项目名。"""

        labelled = cls._label(text, "项目名称")
        if labelled:
            return labelled
        intro = re.search(
            r"(?m)^\s*([^\n]{2,220}?)\s*[（(](?:招标|采购)?项目编号\s*[：:]",
            text,
        )
        if intro:
            return intro.group(1).strip(" ：:")
        first = next((line.strip() for line in text.splitlines() if line.strip()), "")
        return re.sub(
            r"(?:招标公告|采购公告|中标候选人公示|成交候选人公示|"
            r"承租候选人公示|竞租候选人公示|中标结果公示|中标结果公告|"
            r"成交结果公告|结果公告|废标结果公示|流标公告|终止公告)$",
            "",
            first,
        ).strip()

    @classmethod
    def _tender_amount(cls, text: str) -> str:
        labelled = cls._label(
            text, "招标控制价总价", "招标控制价", "最高投标限价",
            "最高含税限价", "最高限价", "预算金额", "招标金额",
        )
        if labelled:
            return labelled
        matched = re.search(
            r"(?:最高含税限价|最高投标限价|最高限价|\u8d77始价)\s*(?:为)?\s*"
            r"([¥￥]?[\d,.]+\s*(?:亿元|万元|元))",
            text,
        )
        if matched:
            return matched.group(1).strip()
        multiline = re.search(
            r"(?:最高投标限价|招标控制价)(?:为)?\s*[：:]\s*\n"
            r"(?:[^\n]{1,80}[：:]\s*)?([\d,.]+\s*(?:万元|元))",
            text,
        )
        return multiline.group(1).strip() if multiline else ""

    @classmethod
    def _clean_award_price(cls, value: str, text: str) -> str:
        price = str(value or "").strip()
        if "换算后中标金额" in price and re.search(r"[：:]\s*/", price):
            body_price = cls._label(
                text, "中标价（折扣系数）", "中标价(折扣系数)", "中标价"
            )
            return body_price or re.split(r"，\s*换算后中标金额", price, maxsplit=1)[0]
        return price or cls._label(
            text, "中标价（折扣系数）", "中标价(折扣系数)",
            "中标价", "中标价格", "中标金额", "成交金额",
        )

    @classmethod
    def _award_parties(cls, text: str, fallback_name: str) -> tuple[str, list[str]]:
        name = str(fallback_name or "").strip()
        members: list[str] = []
        lead = cls._label(text, "联合体牵头人")
        if lead:
            name = lead
        winner = cls._label(text, "中标人", "成交供应商名称")
        if winner:
            cleaned = re.sub(r"^(?:联合体)?牵头人\s*[：:]\s*", "", winner).strip()
            parties = [part.strip() for part in re.split(r"\s*/\s*", cleaned) if part.strip()]
            if parties:
                name = re.sub(
                    r"\s*[（(](?:联合体)?牵头人[）)]\s*$", "", parties[0]
                ).strip()
                members.extend(
                    re.sub(r"\s*[（(]联合体[）)]\s*$", "", part).strip()
                    for part in parties[1:]
                )
        members.extend(cls._list_label(text, "联合体成员"))
        members.extend(cls._list_label(text, "联合体单位"))
        for matched in re.finditer(
            r"(?m)^\s*([^\n]{2,150}?(?:公司|集团|院|中心|事务所))\s*"
            r"[（(]联合体成员[）)]\s*$",
            text,
        ):
            members.append(matched.group(1).strip())
        unique = list(dict.fromkeys(member for member in members if member and member != name))
        return name, unique

    @classmethod
    def _evaluation_method(cls, text: str) -> str:
        labelled = cls._label(text, "评标办法", "评审办法")
        methods = (
            "双信封技术评分最低标价法", "双信封综合评分法", "技术评分最低标价法",
            "双信封合理低价法", "综合评分法", "经评审的最低投标价法", "最低评标价法",
        )
        return next((method for method in methods if method in (labelled or text)), labelled)

    @classmethod
    def _deadline(cls, text: str) -> str:
        value = cls._last_label(
            text, "投标文件递交截止时间", "响应文件递交截止时间",
            "递交截止时间", "投标截止时间", "竞价截止时间",
        ) or cls._narrative_time(text, "竞价截止时间")
        if not value:
            return ""
        # 时间后常紧跟投标文件上传说明，该说明不属于时间字段。
        return re.split(r"[，,] ?\s*(?=投标人|供应商|响应人)", value, maxsplit=1)[0].strip()

    @staticmethod
    def _time_identity(value: str) -> str:
        numbers = re.findall(r"\d+", str(value or ""))
        if len(numbers) < 3:
            return ""
        padded = numbers[:6] + ["0"] * max(0, 6 - len(numbers))
        return "-".join(str(int(part)) for part in padded[:6])

    @staticmethod
    def _narrative_time(text: str, label: str) -> str:
        matched = re.search(
            rf"{re.escape(label)}\s*(?:为|是)\s*([^\n。；;]+)", text
        )
        if not matched:
            return ""
        value = matched.group(1).strip()
        # 竞价模板常写成“2026 年 8 月 06 日 10 时 00 分”，
        # 合并日期数字与中文单位间的排版空格，便于 Schema 转换。
        return re.sub(r"(?:(?<=\d)|(?<=[年月日时分秒]))\s+(?=\d|[年月日时分秒])", "", value)

    @classmethod
    def _guarantee(cls, text: str) -> str:
        direct = cls._label(text, "投标保证金方式", "保证金递交方式")
        if direct:
            return direct
        matched = re.search(r"(?s)提交投标保证金的形式\s*(.*?)(?=\n\s*(?:[八九十]+[、.]|\d+[、.])|\Z)", text)
        return _space(matched.group(1)) if matched else ""

    @classmethod
    def _candidate_table(cls, root) -> list[dict[str, str]]:
        fallback: list[dict[str, str]] = []
        # 每张表独立解析。旧实现把后续“项目负责人/响应情况”表继续拼到
        # 报价表后，造成同一候选人重复且把姓名、工期当成报价。
        for table in root.cssselect("#content table"):
            if table.xpath("ancestor::table"):
                continue
            rows = [
                [_space("".join(cell.itertext())) for cell in row.xpath("./th|./td")]
                for row in table.xpath(".//tr")
            ]
            rows = [row for row in rows if row]
            for index, header in enumerate(rows):
                name_index = next(
                    (
                        i for i, value in enumerate(header)
                        if "候选人" in value
                        and any(k in value for k in ("名称", "中标候选人", "成交候选人"))
                    ),
                    -1,
                )
                if name_index < 0:
                    continue
                price_index = next(
                    (
                        i for i, value in enumerate(header)
                        if any(k in value for k in ("投标报价", "响应报价", "报价", "含税总价", "总价", "金额", "价格"))
                    ),
                    -1,
                )
                result: list[dict[str, str]] = []
                seen: set[str] = set()
                for values in rows[index + 1:]:
                    if name_index >= len(values):
                        continue
                    name = values[name_index].strip()
                    if not name or "候选人" in name or name in seen:
                        continue
                    if not re.search(r"公司|集团|院|中心|事务所|联合体", name):
                        continue
                    seen.add(name)
                    price = values[price_index].strip() if 0 <= price_index < len(values) else ""
                    result.append({"标段": "", "候选人名称": name, "候选人报价": price})
                if result and price_index >= 0:
                    return result
                if result and not fallback:
                    fallback = result
        return fallback

    @classmethod
    def _candidate_text(cls, text: str) -> list[dict[str, str]]:
        pattern = re.compile(
            r"(?m)^\s*(?:(?:第?[一二三123]\s*)?中标候选人|"
            r"中标候选人\s*[一二三123])(?:名称)?\s*[：:]\s*([^\n]{2,180})"
        )
        result = []
        for match in pattern.finditer(text):
            line_value = match.group(1).strip(" ：:；;")
            name = re.split(
                r"\s+(?=(?:投标报价|响应报价|报价|中标价|中标金额|成交金额)\s*[：:])",
                line_value,
                maxsplit=1,
            )[0].strip(" ：:；;")
            tail = text[match.end():match.end() + 400]
            price_source = line_value[len(name):] + "\n" + tail
            price = cls._label(
                price_source, "投标报价", "响应报价", "报价",
                "中标价", "中标金额", "成交金额",
            )
            if name and not any(row["候选人名称"] == name for row in result):
                result.append({"标段": "", "候选人名称": name, "候选人报价": price})
        return result or cls._candidate_details(text)

    @classmethod
    def _award_text(cls, text: str) -> list[dict[str, str]]:
        normalized = re.sub(r"中\s*标\s*人", "中标人", text)
        rows = cls._award_details(normalized)
        if rows:
            return rows
        matched = re.search(r"(?:确定|成交供应商(?:名称)?[：:]?)\s*([^\n，,。；;]{2,150}?(?:公司|集团|院|中心|事务所))", normalized)
        if not matched:
            return []
        price = cls._label(normalized, "中标价", "中标价格", "中标金额", "成交金额")
        return [{"标段": "", "中标人名称": matched.group(1).strip(), "中标价": price}]

    @staticmethod
    def _attachments(root) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        seen: set[str] = set()
        for node in root.xpath(".//*[@onclick]"):
            onclick = str(node.get("onclick") or "")
            matched = re.search(r"downloadFile\(['\"]?([A-Za-z0-9_-]+)['\"]?\)", onclick)
            if not matched or matched.group(1) in seen:
                continue
            file_id = matched.group(1)
            seen.add(file_id)
            name = _space("".join(node.itertext())) or f"attachment_{file_id}"
            file_type = mimetypes.guess_type(name)[0] or "application/octet-stream"
            result.append({
                "source_file_id": file_id,
                "file_name": name,
                "file_url": config.attachment_url(file_id),
                "file_type": file_type,
                "parse_status": "PENDING",
                "source": "detail_downloadFile",
            })
        return result
