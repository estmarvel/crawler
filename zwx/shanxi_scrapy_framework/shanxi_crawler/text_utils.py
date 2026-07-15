import re
from shanxi_crawler.columns import DEFAULT_VALUE


def normalize_text(text: str) -> str:
    if not text:
        return ""
    text = str(text).replace("\xa0", " ").replace("&nbsp;", " ").replace("\u3000", " ")
    lines = []
    for line in text.splitlines():
        line = re.sub(r"[ \t\r\f\v]+", " ", line).strip()
        if line:
            lines.append(line)
    return "\n".join(lines)


def normalize_inline(text: str) -> str:
    if not text:
        return ""
    text = str(text).replace("\xa0", " ").replace("&nbsp;", " ").replace("\u3000", " ")
    text = re.sub(r"\s+", " ", text).strip()
    return text.strip("：:，,；;。 ")


def clean_value(value: str) -> str:
    value = normalize_inline(str(value or ""))
    if not value:
        return DEFAULT_VALUE
    for kw in ["备案号", "晋ICP", "网站标识码", "晋公网安备", "山西省行政审批服务管理局"]:
        pos = value.find(kw)
        if pos != -1:
            value = value[:pos].strip()
    return value or DEFAULT_VALUE


def is_empty_value(v) -> bool:
    s = normalize_inline(str(v or ""))
    return s in ("", DEFAULT_VALUE, "暂无", "None", "nan", "null", "NULL")


def append_unique(existing: str, new_part: str) -> str:
    new_part = normalize_inline(new_part)
    if not new_part:
        return existing if not is_empty_value(existing) else DEFAULT_VALUE
    existing = str(existing or DEFAULT_VALUE)
    if is_empty_value(existing):
        return new_part
    parts = [p.strip() for p in re.split(r"[；;]", existing) if p.strip()]
    if new_part not in parts:
        parts.append(new_part)
    return "；".join(parts)


def format_publish_date(date_text: str) -> str:
    s = normalize_inline(str(date_text or ""))
    if not s:
        return ""
    m = re.search(r"(\d{4})[-/.年](\d{1,2})[-/.月](\d{1,2})", s)
    if not m:
        return s
    return f"{m.group(1)}.{int(m.group(2))}.{int(m.group(3))}"


def make_publish_part(label: str, date_text: str) -> str:
    label = normalize_inline(label)
    date = format_publish_date(date_text)
    if not label or not date:
        return ""
    return f"{label}-{date}"


def normalize_publish_cell(cell: str) -> str:
    cell = normalize_inline(str(cell or ""))
    if is_empty_value(cell):
        return DEFAULT_VALUE
    valid_types = [
        "招标计划", "招标公告", "资格预审公告", "变更公告", "更正公告", "澄清公告", "延期公告",
        "中标候选人公示", "中标结果公示", "终止公告", "废标公告", "撤销公告",
    ]
    out = []
    for part in re.split(r"[；;]", cell):
        part = normalize_inline(part)
        m = re.fullmatch(r"(.+?)-(\d{4}\.\d{1,2}\.\d{1,2})", part)
        if not m:
            continue
        label = normalize_inline(m.group(1))
        date = normalize_inline(m.group(2))
        if label not in valid_types:
            continue
        new_part = f"{label}-{date}"
        if new_part not in out:
            out.append(new_part)
    return "；".join(out) if out else DEFAULT_VALUE


def merge_into_row(row: dict, updates: dict) -> dict:
    keep_first = {"公告内容", "开标时间", "标书发售时间"}
    for k, v in (updates or {}).items():
        v = clean_value(v)
        if is_empty_value(v):
            continue
        old = row.get(k, DEFAULT_VALUE)
        if is_empty_value(old):
            row[k] = v
        elif k not in keep_first:
            row[k] = v
    return row
