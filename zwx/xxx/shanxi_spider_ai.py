"""
山西省公共资源交易平台爬虫 v3.4
新增：AI辅助字段提取（类型1智能分类 + 类型2字段补全）
"""

import requests
from bs4 import BeautifulSoup
import pandas as pd
import time, random, re, os, json
from datetime import datetime
from openai import OpenAI

# ==================== 配置 ====================
BASE_URL = "https://prec.sxzwfw.gov.cn"
LIST_URL = "https://prec.sxzwfw.gov.cn/queryContent-jyxx.jspx"

CHANNEL = {
    "招标计划":   198,
    "招标公告":   12,
    "变更公告":   13,
    "中标候选人": 14,
    "中标结果":   15,
}

OUTPUT_DIR = "/home/intsig/zwx/shanxi/output"
os.makedirs(OUTPUT_DIR, exist_ok=True)

TODAY_STR = datetime.now().strftime("%Y%m%d")
CSV_PATH = os.path.join(OUTPUT_DIR, f"{TODAY_STR}_山西省.csv")
LOG_DIR = "/home/intsig/zwx/shanxi/log"
os.makedirs(LOG_DIR, exist_ok=True)
CHECK_LOG_PATH  = os.path.join(LOG_DIR, f"{TODAY_STR}_check.log")
AI_REPORT_PATH  = os.path.join(LOG_DIR, f"{TODAY_STR}_ai_report.log")  # AI补全报告

CRAWL_DAYS = 1   # 改为只爬当天

SESSION = requests.Session()

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Referer": "https://prec.sxzwfw.gov.cn/jyxx/index.jhtml",
    "Content-Type": "application/x-www-form-urlencoded",
}

COLUMNS = [
     "项目名称", "交易地区", "统一代码","项目类型", "招标内容",
    "招标人名称", "发布类型", "建设地点", "建设内容及规模",
    "项目总投资", "招标方式", "行政监督部门", "发布单位",
    "招标公告发布日期", "招标文件获取时间", "投标人资格要求", "招标公告链接",
    "变更公告链接", "中标候选人名称", "中标候选人公示链接",
    "中标人", "中标结果公示链接", "终止公告链接", "公告历史"
]

SKIP_KEYWORDS      = ["招标控制价变更", "招标控制价", "控制价变更", "控制价"]
TERMINATE_KEYWORDS = ["终止公告", "废标公告", "终止招标", "撤销公告", "招标公告撤销公告", "招标撤销公告"]
AMENDMENT_KEYWORDS = ["变更公告", "更正公告", "变更公示", "更正公示", "澄清公告", "澄清答疑", "延期公告", "二次延期公告"]
NOTICE_KEYWORDS    = ["招标公告", "二次招标公告", "二次重新招标公告", "三次招标公告", "三次重新招标公告", "预审公告", "资审公告"]
CANDIDATE_KEYWORDS = ["中标候选人公示"]
RESULT_KEYWORDS    = ["中标结果公示澄清", "中标结果公示"]

SKIPPED_UNKNOWN = []

# ==================== AI 配置 ====================
AI_API_KEY = "sk-b5WCpX7UhAjiFXrc6BjrttdAmNgkNPVO2K8aDPM51gvfHVtr"  
AI_MODEL   = "gpt-4o-mini"                                      
AI_BASE_URL = "https://vip.dmxapi.com/v1"

# ==================== AI Token 计数器 ====================

class _TokenCounter:
    """全局 token 用量累加器"""
    def __init__(self):
        self.prompt_tokens     = 0
        self.completion_tokens = 0
        self.total_tokens      = 0
        self.call_count        = 0

    def add(self, usage):
        if not usage:
            return
        # usage 可能是对象或 dict
        if hasattr(usage, "prompt_tokens"):
            self.prompt_tokens     += usage.prompt_tokens or 0
            self.completion_tokens += usage.completion_tokens or 0
            self.total_tokens      += usage.total_tokens or 0
        else:
            self.prompt_tokens     += usage.get("prompt_tokens", 0)
            self.completion_tokens += usage.get("completion_tokens", 0)
            self.total_tokens      += usage.get("total_tokens", 0)
        self.call_count += 1

    def report(self):
        return (
            f"AI调用统计：共 {self.call_count} 次\n"
            f"  输入 tokens : {self.prompt_tokens:,}\n"
            f"  输出 tokens : {self.completion_tokens:,}\n"
            f"  合计 tokens : {self.total_tokens:,}"
        )

TOKEN_COUNTER = _TokenCounter()

# ==================== AI 核心提取函数（对外封装） ====================

def ai_extract_fields(html_or_text: str, fields: list, project_name: str = "", retries: int = 2) -> dict:
    """
    【封装函数】调用大模型从公告 HTML/纯文本中提取指定字段。

    可直接在其他爬虫脚本中 import 并调用：
        from shanxi_spider import ai_extract_fields, TOKEN_COUNTER

    参数
    ----
    html_or_text : str
        公告详情页的原始 HTML 或 soup.get_text() 纯文本，两种都支持。
    fields : list[str]
        需要提取的字段名列表，例如：
        ["投标人资格要求", "招标文件获取时间", "中标候选人名称", "中标人"]
    project_name : str
        项目名称，仅用于日志提示，可不传。
    retries : int
        失败后最多重试次数（默认 2）。

    返回
    ----
    dict : {字段名: 提取到的值字符串}
        提取失败或文中不存在的字段值为空字符串 ""。

    副作用
    ------
    每次成功调用自动更新全局 TOKEN_COUNTER。
    """
    if not html_or_text or not fields:
        return {f: "" for f in fields}

    # 优先用 BeautifulSoup 提取纯文本，减少 token
    try:
        clean = BeautifulSoup(html_or_text, "html.parser").get_text(separator="\n")
    except Exception:
        clean = html_or_text

    fields_str = "、".join(fields)
    system_prompt = (
        "你是一个招标公告信息提取助手，只负责从公告文本中精确抽取指定字段。\n"
        "规则：\n"
        "1. 只返回 JSON 对象，不要有任何解释、前缀或代码块标记（不要```）。\n"
        "2. JSON 的 key 为字段名，value 为提取到的完整文本内容。\n"
        "3. 字段在文中找不到时，value 设为空字符串 \"\"，不要猜测或捏造。\n"
        "4. 提取「投标人资格要求」：提取完整资格条件段落（含证书、资质等级、业绩等要求），不要只提取标题行。\n"
        "5. 提取「招标文件获取时间」：提取具体时间区间文本，保留完整时间描述。\n"
        "6. 提取「中标候选人名称」：如有多个候选人，用顿号「、」连接。\n"
        "7. 提取「中标人」：提取完整公司名称，多个用顿号「、」连接。\n"
        "8. 不要在 value 中包含字段名本身。"
    )
    user_prompt = (
        f"项目名称：{project_name}\n"
        f"需要提取的字段：{fields_str}\n\n"
        f"公告文本：\n{clean}"
    )

    client = OpenAI(api_key=AI_API_KEY, base_url=AI_BASE_URL)

    for attempt in range(retries + 1):
        try:
            resp = client.chat.completions.create(
                model=AI_MODEL,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user",   "content": user_prompt},
                ],
                temperature=0.0,
                max_tokens=4000,
            )
            TOKEN_COUNTER.add(resp.usage)

            raw = resp.choices[0].message.content.strip()
            # 去掉偶发的 ```json ... ``` 包裹
            raw = re.sub(r"^```(?:json)?\s*", "", raw)
            raw = re.sub(r"\s*```$", "", raw)

            result = json.loads(raw)
            # 补全未返回的字段
            for f in fields:
                if f not in result:
                    result[f] = ""
            return result

        except json.JSONDecodeError as e:
            print(f"    [AI] JSON解析失败(第{attempt+1}次): {e} | 原始: {raw[:100]}")
        except Exception as e:
            print(f"    [AI] 调用失败(第{attempt+1}次): {e}")

        if attempt < retries:
            time.sleep(3)

    return {f: "" for f in fields}


# ==================== AI 兜底补全（读 check.log → 重爬 → 写 CSV + AI报告）====================

def ai_supplement(df: pd.DataFrame) -> pd.DataFrame:
    """
    读取当天 check.log 中的「类型2：有链接但字段提取失败」列表，
    对每条失败记录重新抓取详情页 HTML，调用 ai_extract_fields() 补全字段，
    最后生成 ai_report.log 并返回更新后的 DataFrame。

    流程：build_check_log() → ai_supplement() → save_csv() → AI报告
    """
    print(f"\n===== AI辅助补全（读取 {CHECK_LOG_PATH}）=====")

    if not os.path.exists(CHECK_LOG_PATH):
        print("  check.log 不存在，跳过AI补全")
        return df

    # ---------- 解析 check.log，提取类型2的失败项 ----------
    with open(CHECK_LOG_PATH, "r", encoding="utf-8") as f:
        content = f.read()

    # 找到类型2段落
    m = re.search(r"【类型2】有链接但字段提取失败\n-+\n(.*?)(?=\n【|$)", content, re.DOTALL)
    if not m or m.group(1).strip() == "无":
        print("  check.log 类型2：无失败项，跳过AI补全")
        _write_ai_report([], 0)
        return df

    block = m.group(1)

    # 解析每个项目及其失败字段
    # 格式：
    #   项目: <名称>
    #     - <字段>未提取
    tasks = []  # [{"name": ..., "issues": [...]}]
    current = None
    for line in block.splitlines():
        line = line.strip()
        if line.startswith("项目:"):
            if current:
                tasks.append(current)
            current = {"name": line[3:].strip(), "issues": []}
        elif line.startswith("- ") and current is not None:
            current["issues"].append(line[2:].strip())
    if current:
        tasks.append(current)

    if not tasks:
        print("  未解析到失败项，跳过AI补全")
        _write_ai_report([], 0)
        return df

    print(f"  共 {len(tasks)} 个项目需要AI补全")

    # ---------- 字段名映射：issue描述 → (CSV字段名, 数据来源列) ----------
    ISSUE_TO_FIELD = {
        "投标人资格要求未提取":    ("投标人资格要求",    "招标公告链接"),
        "招标文件获取时间未提取":  ("招标文件获取时间",  "招标公告链接"),
        "中标候选人名称未提取":    ("中标候选人名称",    "中标候选人公示链接"),
        "中标人未提取":            ("中标人",            "中标结果公示链接"),
    }

    report_records = []  # 记录每次AI操作结果
    updated_count  = 0

    for task in tasks:
        project_name = task["name"]
        issues       = task["issues"]

        # 找到 df 中对应行
        idx = find_row(df, project_name)
        if idx < 0:
            print(f"  [{project_name}] 未在CSV中找到，跳过")
            report_records.append({
                "project": project_name,
                "fields":  issues,
                "status":  "跳过（CSV中未找到该项目）",
                "values":  {},
            })
            continue

        # 按 link 列分组，同一个 URL 只请求一次 HTML
        url_to_fields = {}  # {url: [字段名, ...]}
        for issue in issues:
            if issue not in ISSUE_TO_FIELD:
                continue
            field_name, link_col = ISSUE_TO_FIELD[issue]
            url = str(df.at[idx, link_col])
            if url == "暂无" or not url.startswith("http"):
                continue
            url_to_fields.setdefault(url, []).append(field_name)

        if not url_to_fields:
            report_records.append({
                "project": project_name,
                "fields":  issues,
                "status":  "跳过（无有效链接）",
                "values":  {},
            })
            continue

        project_values  = {}
        project_success = True

        for url, fields_needed in url_to_fields.items():
            print(f"  [{project_name}] AI提取 {fields_needed} ← {url}")
            sleep()

            # 抓 HTML
            resp = get(url)
            if not resp:
                print(f"    [AI] 页面请求失败，跳过")
                project_success = False
                for f in fields_needed:
                    project_values[f] = ""
                continue

            # 调用 AI
            ai_result = ai_extract_fields(resp.text, fields_needed, project_name)

            # 写入 df
            for field_name in fields_needed:
                val = ai_result.get(field_name, "").strip()
                project_values[field_name] = val
                if val:
                    df.at[idx, field_name] = val
                    updated_count += 1
                    print(f"    ✓ {field_name}: {val[:60]}")
                else:
                    print(f"    ✗ {field_name}: AI未能提取")
                    project_success = False

        status = "成功" if project_success and all(project_values.values()) else (
            "部分成功" if any(project_values.values()) else "失败"
        )
        report_records.append({
            "project": project_name,
            "fields":  [ISSUE_TO_FIELD[i][0] for i in issues if i in ISSUE_TO_FIELD],
            "status":  status,
            "values":  project_values,
        })

    # ---------- 写入 AI 报告 ----------
    _write_ai_report(report_records, updated_count)

    print(f"\n  AI补全完成：{updated_count} 个字段成功补全 → {AI_REPORT_PATH}")
    print(f"  {TOKEN_COUNTER.report()}")

    return df


def _write_ai_report(records: list, updated_count: int):
    """生成 ai_report.log"""
    success  = sum(1 for r in records if r["status"] == "成功")
    partial  = sum(1 for r in records if r["status"] == "部分成功")
    failed   = sum(1 for r in records if r["status"] == "失败")
    skipped  = sum(1 for r in records if r["status"].startswith("跳过"))

    with open(AI_REPORT_PATH, "w", encoding="utf-8") as f:
        f.write("山西省公共资源交易平台 AI补全报告\n")
        f.write(f"生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
        f.write(f"使用模型: {AI_MODEL}\n")
        f.write("=" * 60 + "\n\n")

        f.write("【汇总】\n")
        f.write(f"  处理项目数 : {len(records)}\n")
        f.write(f"  成功        : {success}\n")
        f.write(f"  部分成功    : {partial}\n")
        f.write(f"  失败        : {failed}\n")
        f.write(f"  跳过        : {skipped}\n")
        f.write(f"  字段补全数  : {updated_count}\n\n")

        f.write("【Token消耗】\n")
        f.write(f"  AI调用次数  : {TOKEN_COUNTER.call_count}\n")
        f.write(f"  输入tokens  : {TOKEN_COUNTER.prompt_tokens:,}\n")
        f.write(f"  输出tokens  : {TOKEN_COUNTER.completion_tokens:,}\n")
        f.write(f"  合计tokens  : {TOKEN_COUNTER.total_tokens:,}\n\n")

        f.write("【详细记录】\n")
        f.write("-" * 40 + "\n")
        for r in records:
            f.write(f"项目: {r['project']}\n")
            f.write(f"  目标字段 : {', '.join(r['fields'])}\n")
            f.write(f"  结果     : {r['status']}\n")
            if r["values"]:
                for field, val in r["values"].items():
                    display_val = (val[:80] + "...") if len(val) > 80 else val
                    mark = "✓" if val else "✗"
                    f.write(f"  {mark} {field}: {display_val if val else '（未提取到）'}\n")
            f.write("\n")

    print(f"[AI报告] → {AI_REPORT_PATH}")


# ==================== AI 类型1：未识别公告智能分类 ====================

# 告诉 AI 每种类型对应的中文描述，便于它理解和判断
_TYPE_DESC = {
    "skip":      "控制价公告（招标控制价变更、控制价发布等，与投标无关，直接跳过）",
    "terminate": "终止/废标/撤销公告（项目终止招标、废标、撤销招标等）",
    "amendment": "变更/更正/澄清/延期公告（对已发布招标公告的修改、澄清或延期）",
    "notice":    "招标公告/预审公告/资审公告（正式发布的招标或资格预审公告）",
    "candidate": "中标候选人公示（公示评标结果、候选中标人名单）",
    "result":    "中标结果公示（最终确定中标人的公告）",
}

def ai_classify_title(raw_title: str, detail_text: str, retries: int = 2) -> str:
    """
    【内部函数】调用 AI 对单条未识别公告进行分类。

    参数
    ----
    raw_title   : 公告原始标题
    detail_text : 详情页纯文本（用 soup.get_text() 提取，传前两千字足够）
    retries     : 失败重试次数

    返回
    ----
    str : 分类结果，取值为 skip / terminate / amendment / notice / candidate / result / unknown
    """
    type_list = "\n".join(f"- {k}：{v}" for k, v in _TYPE_DESC.items())
    system_prompt = (
        "你是一个招标公告分类助手。我会给你一条公告的标题和详情页部分内容，"
        "请判断它属于以下哪种类型，只返回类型的英文 key，不要有任何其他内容。\n\n"
        f"类型说明：\n{type_list}\n\n"
        "如果都不符合，返回 unknown。"
    )
    user_prompt = (
        f"公告标题：{raw_title}\n\n"
        f"详情页内容（前2000字）：\n{detail_text[:2000]}"
    )

    client = OpenAI(api_key=AI_API_KEY, base_url=AI_BASE_URL)
    valid_types = set(_TYPE_DESC.keys()) | {"unknown"}

    for attempt in range(retries + 1):
        try:
            resp = client.chat.completions.create(
                model=AI_MODEL,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user",   "content": user_prompt},
                ],
                temperature=0.0,
                max_tokens=20,
            )
            TOKEN_COUNTER.add(resp.usage)
            result = resp.choices[0].message.content.strip().lower()
            if result in valid_types:
                return result
            # 容错：AI 可能返回带引号或多余空格
            result = result.strip('"\'` ')
            if result in valid_types:
                return result
            print(f"    [AI分类] 返回值无效: {result!r}，重试")
        except Exception as e:
            print(f"    [AI分类] 调用失败(第{attempt+1}次): {e}")
        if attempt < retries:
            time.sleep(3)

    return "unknown"


def ai_classify_and_handle(df: pd.DataFrame) -> pd.DataFrame:
    """
    读取当天 check.log 中的「类型1：未识别公告」列表，
    对每条公告抓取详情页，调用 AI 判断类型，
    然后走对应的 _handle_xxx() 处理函数写入 DataFrame。

    在 main() 中于 build_check_log() 之后、ai_supplement() 之前调用。
    """
    print(f"\n===== AI智能分类（类型1未识别公告）=====")

    if not os.path.exists(CHECK_LOG_PATH):
        print("  check.log 不存在，跳过")
        return df

    with open(CHECK_LOG_PATH, "r", encoding="utf-8") as f:
        content = f.read()

    # 解析类型1段落
    m = re.search(r"【类型1】未识别公告（共\d+条）\n-+\n(.*?)(?=\n【|$)", content, re.DOTALL)
    if not m or m.group(1).strip() == "无":
        print("  类型1：无未识别公告，跳过")
        return df

    block = m.group(1)

    # 解析每条记录，格式：
    #   标题: <raw_title>
    #   链接: <url>
    unknowns = []
    current = {}
    for line in block.splitlines():
        line = line.strip()
        if line.startswith("标题:"):
            current = {"raw_title": line[3:].strip(), "link": ""}
        elif line.startswith("链接:") and current:
            current["link"] = line[3:].strip()
            if current["raw_title"] and current["link"]:
                unknowns.append(current)
            current = {}

    if not unknowns:
        print("  未解析到未识别公告，跳过")
        return df

    print(f"  共 {len(unknowns)} 条未识别公告需要AI分类")

    classify_records = []  # 用于写报告

    for item in unknowns:
        raw_title = item["raw_title"]
        link      = item["link"]
        pure_title = clean_title(raw_title)

        print(f"\n  [{raw_title}]")

        # 构造一个和五步爬取相同格式的 item dict
        # location 先设暂无，AI分类后 _handle_xxx 内部会通过历史搜索补全
        fake_item = {
            "raw_title": raw_title,
            "title":     pure_title,
            "link":      link,
            "date":      "",
            "location":  "暂无",
        }

        # 抓详情页
        sleep()
        resp = get(link)
        if not resp:
            print(f"    页面请求失败，跳过")
            classify_records.append({
                "raw_title": raw_title,
                "link":      link,
                "ai_type":   "请求失败",
                "handled":   False,
            })
            continue

        detail_text = BeautifulSoup(resp.text, "html.parser").get_text(separator="\n")

        # AI 分类
        ai_type = ai_classify_title(raw_title, detail_text)
        print(f"    AI分类结果: {ai_type}（{_TYPE_DESC.get(ai_type, '未知')}）")

        handled = True
        try:
            if ai_type == "skip":
                print(f"    → 跳过（控制价）")

            elif ai_type == "terminate":
                df = _handle_terminate(df, fake_item)
                save_csv(df)

            elif ai_type == "amendment":
                df = _handle_amendment(df, fake_item, from_step=3)
                save_csv(df)

            elif ai_type == "notice":
                # 走正则处理流程，字段缺失的交给后续 ai_supplement 统一兜底
                df = _handle_notice(df, fake_item)
                save_csv(df)

            elif ai_type == "candidate":
                df = _handle_candidate(df, fake_item, from_step=4)
                save_csv(df)

            elif ai_type == "result":
                df = _handle_result(df, fake_item, from_step=5)
                save_csv(df)

            else:
                # unknown：AI 也判断不了，记录在案，人工处理
                print(f"    → AI仍无法分类，记录存档待人工处理")
                handled = False

        except Exception as e:
            print(f"    处理时出错: {e}")
            handled = False

        classify_records.append({
            "raw_title": raw_title,
            "link":      link,
            "ai_type":   ai_type,
            "handled":   handled,
        })

    # 把分类结果追加写入 AI 报告
    _append_classify_report(classify_records)

    handled_count = sum(1 for r in classify_records if r["handled"])
    print(f"\n  AI分类完成：{handled_count}/{len(unknowns)} 条成功处理")

    return df


def _append_classify_report(records: list):
    """把类型1的分类结果追加写入已有的 ai_report.log"""
    mode = "a" if os.path.exists(AI_REPORT_PATH) else "w"
    with open(AI_REPORT_PATH, mode, encoding="utf-8") as f:
        f.write("\n")
        f.write("【类型1：未识别公告AI分类结果】\n")
        f.write("-" * 40 + "\n")
        for r in records:
            mark = "✓" if r["handled"] else "✗"
            type_desc = _TYPE_DESC.get(r["ai_type"], r["ai_type"])
            f.write(f"{mark} {r['raw_title']}\n")
            f.write(f"   链接    : {r['link']}\n")
            f.write(f"   AI分类  : {r['ai_type']}（{type_desc}）\n")
            f.write(f"   处理结果: {'已处理' if r['handled'] else '未处理，需人工介入'}\n\n")
    print(f"[AI报告] 类型1分类结果已追加 → {AI_REPORT_PATH}")


# ==================== 公告分类 ====================

def classify_title(raw_title):
    for kw in SKIP_KEYWORDS:
        if kw in raw_title:
            return "skip"
    for kw in TERMINATE_KEYWORDS:
        if kw in raw_title:
            return "terminate"
    for kw in AMENDMENT_KEYWORDS:
        if kw in raw_title:
            return "amendment"
    for kw in RESULT_KEYWORDS:
        if kw in raw_title:
            return "result"
    for kw in CANDIDATE_KEYWORDS:
        if kw in raw_title:
            return "candidate"
    for kw in NOTICE_KEYWORDS:
        if kw in raw_title:
            return "notice"
    return "unknown"

# ==================== 工具函数 ====================

def sleep():
    time.sleep(random.uniform(1.0, 2.0))

def get(url, retries=3):
    for i in range(retries):
        try:
            resp = SESSION.get(url, headers=HEADERS, timeout=15)
            resp.encoding = "utf-8"
            return resp
        except Exception as e:
            print(f"  [GET失败{i+1}/{retries}] {e}")
            time.sleep(2)
    return None

def post_req(url, data, retries=3, headers=None):
    h = headers if headers else HEADERS
    for i in range(retries):
        try:
            resp = SESSION.post(url, headers=h, data=data, timeout=30)
            resp.encoding = "utf-8"
            return resp
        except Exception as e:
            print(f"  [POST失败{i+1}/{retries}] {e}")
            time.sleep(3)
    return None

def clean_title(title):
    suffixes = [
        "中标结果公示", "中标结果公示澄清", "中标候选人公示",
        "变更公告", "更正公告", "变更公示", "更正公示", "澄清公告", "澄清答疑", "延期公告", "二次延期公告",
        "招标计划",
        "招标控制价变更", "招标控制价", "控制价变更", "控制价",
        "招标公告", "二次招标公告", "二次重新招标公告", "三次招标公告", "三次重新招标公告",
        "预审公告", "撤销公告", "招标公告撤销公告", "招标撤销公告", "终止公告", "废标公告",
        "其他公告",
    ]
    t = title.strip()
    for s in suffixes:
        if t.endswith(s):
            t = t[:-len(s)].strip()
            break
    return t

def should_skip(raw_title):
    return classify_title(raw_title) == "skip"

def should_terminate(raw_title):
    return classify_title(raw_title) == "terminate"

def is_amendment(raw_title):
    return classify_title(raw_title) == "amendment"

def parse_location(loc_text):
    loc_text = re.sub(r"交易场所[：:]", "", loc_text).strip()
    if "省本级" in loc_text or loc_text == "省":
        return "山西省本级"
    m = re.search(r'([^\s]+市)', loc_text)
    if m:
        return m.group(1)
    return loc_text or "暂无"

def empty_row(project_name, location="暂无"):
    row = {col: "暂无" for col in COLUMNS}
    row["项目名称"] = project_name
    row["交易地区"] = location
    return row

# ==================== 列表页爬取 ====================

def fetch_list(channel_id, days=4, search_title="", verbose=True):
    results = []
    page = 1
    prev_url = "https://prec.sxzwfw.gov.cn/jyxx/index.jhtml"
    while True:
        if page == 1:
            url = LIST_URL
        else:
            url = LIST_URL.replace("queryContent-", f"queryContent_{page}-")

        data = {
            "title": search_title,
            "channelId": channel_id,
            "inDates": days,
            "beginTime": "", "endTime": "", "origin": "", "ext": "",
        }
        headers = dict(HEADERS)
        headers["Referer"] = prev_url
        if verbose: print(f"  [列表] channelId={channel_id} 第{page}页...")
        resp = post_req(url, data, headers=headers)
        prev_url = url
        if not resp:
            if verbose: print("  [列表] 请求失败")
            break
        if len(resp.text) < 100:
            if verbose: print(f"  [列表] 响应内容异常(长度={len(resp.text)})，跳过")
            break

        soup = BeautifulSoup(resp.text, "html.parser")
        items = soup.select(".cs_two_c_2")
        if not items:
            if page == 1 and verbose:
                print(f"  [列表] 无数据，状态码={resp.status_code}")
            break

        for item in items:
            link = item.get("href", "")
            if not link.startswith("http"):
                link = BASE_URL + link
            link = link.replace("http://prec.sxzwfw.gov.cn:80", "https://prec.sxzwfw.gov.cn")
            link = link.replace("http://prec.sxzwfw.gov.cn", "https://prec.sxzwfw.gov.cn")

            title_el = item.select_one(".cs_bz_cont")
            raw_title = title_el.get_text(strip=True) if title_el else ""
            pure_title = clean_title(raw_title)

            date_el = item.select_one(".cs_bz_cont_1_time")
            date = date_el.get_text(strip=True) if date_el else ""

            loc_spans = item.select(".cs_bz_cont_1 span")
            loc_text = ""
            for sp in loc_spans:
                t = sp.get_text(strip=True)
                if "交易场所" in t:
                    loc_text = t
                    break
            location = parse_location(loc_text)

            results.append({
                "raw_title": raw_title,
                "title": pure_title,
                "link": link,
                "date": date,
                "location": location,
            })

        count_match = re.search(r",count:\s*(\d+)", resp.text)
        if count_match:
            total = int(count_match.group(1))
            if verbose: print(f"  [列表] 总条数={total}，当前已获取={len(results)}")
            if page * 10 >= total:
                break
        else:
            break

        page += 1
        sleep()

    if verbose: print(f"  [列表] 共 {len(results)} 条")
    return results

# ==================== 详情页解析 ====================

def parse_plan_detail(url):
    resp = get(url)
    if not resp:
        return {}

    soup = BeautifulSoup(resp.text, "html.parser")
    result = {}

    table = soup.select_one("table.bid_msgTable")
    if not table:
        return result

    label_map = {
        "投资项目统一代码": "统一代码",
        "项目类型": "项目类型",
        "项目总投资": "项目总投资",
        "招标内容": "招标内容",
        "招标方式": "招标方式",
        "招标人名称": "招标人名称",
        "行政监督部门": "行政监督部门",
        "发布类型": "发布类型",
        "发布单位": "发布单位",
        "建设地点": "建设地点",
        "建设内容及规模": "建设内容及规模",
    }

    rows = table.select("tr")
    for row in rows:
        tds = row.select("td")
        label_tds = [(i, td) for i, td in enumerate(tds) if td.find("b")]
        for idx, ltd in label_tds:
            label = ltd.get_text(strip=True).rstrip("：: ")
            label_clean = re.sub(r"[（(][^）)]*[）)]", "", label).strip()
            val = ""
            for j in range(idx + 1, len(tds)):
                if not tds[j].find("b"):
                    val = tds[j].get_text(strip=True)
                    break
            if "预计发布时间" in label:
                result["招标公告发布日期"] = val
            elif label_clean in label_map:
                result[label_map[label_clean]] = val

    return result


def parse_notice_detail(url):
    resp = get(url)
    if not resp:
        return {}

    soup = BeautifulSoup(resp.text, "html.parser")
    content_el = soup.select_one("table.gycq-table td") or soup.select_one(".cs_xq_content")
    full_text = content_el.get_text() if content_el else soup.get_text()
    result = {"招标公告链接": url}

    acq_patterns = [
        r'获取时间[：:]\s*([^\n。]{5,80})',
        r'文件获取时间[：:]\s*([^\n。]{5,80})',
    ]
    for p in acq_patterns:
        m = re.search(p, full_text)
        if m:
            val = m.group(1).strip().rstrip("。；")
            if len(val) > 5:
                result["招标文件获取时间"] = val
                break

    STOP_SECTIONS = [
        "招标文件的获取", "招标文件获取", "文件的获取", "电子招标文件",
        "预审文件的获取", "申请文件的递交",
        "投标文件的递交", "投标文件递交", "开标时间", "开标地点",
        "投标保证金", "提出异议", "异议的渠道",
        "联系方式", "监督部门", "招标代理机构",
        "盖章", "备案号", "晋ICP", "行政审批服务管理局", "投诉举报"
    ]
    kw_pos = full_text.find("投标人资格要求")
    if kw_pos == -1:
        kw_pos = full_text.find("投标人资格条件")
    if kw_pos == -1:
        kw_pos = full_text.find("申请人资格要求")
    if kw_pos == -1:
        kw_pos = full_text.find("申请投标人资格要求")
    if kw_pos == -1:
        kw_pos = full_text.find("投标资格要求")
    if kw_pos != -1:
        after = full_text[kw_pos:]
        nl = after.find('\n')
        if nl != -1:
            content_after = after[nl:]
            cut_pos = len(content_after)
            for kw in STOP_SECTIONS:
                p = content_after.find(kw)
                if p != -1 and p < cut_pos:
                    cut_pos = p
            qual_text = re.sub(r'\n+', ' ', content_after[:cut_pos].strip()).strip()
            if len(qual_text) > 20:
                result["投标人资格要求"] = qual_text

    return result


def parse_candidate_detail(url):
    resp = get(url)
    if not resp:
        return {}

    soup = BeautifulSoup(resp.text, "html.parser")
    result = {"中标候选人公示链接": url}

    candidates = []
    tables = soup.select("table")
    SKIP_NAMES = {"中标候选人名称", "中标候选人", "名称", "排序", "序号", ""}

    for table in tables:
        cls_str = " ".join(table.get("class") or [])
        if "gycq-table" in cls_str:
            continue
        all_rows = table.select("tr")
        if len(all_rows) < 2:
            continue
        first_row_cells = all_rows[0].select("td, th")
        if len(first_row_cells) <= 1:
            continue
        header_cells = all_rows[0].select("th") or all_rows[0].select("td")
        headers = [c.get_text(strip=True) for c in header_cells]
        candidate_col = -1
        for ci, h in enumerate(headers):
            if h in ["中标候选人名称", "中标候选人"]:
                candidate_col = ci
                break
        if candidate_col == -1:
            continue
        data_rows = all_rows[1:]
        for row in data_rows:
            cells = row.select("td")
            if len(cells) > candidate_col:
                name = cells[candidate_col].get_text(strip=True)
                if name and len(name) >= 2 and name not in SKIP_NAMES:
                    if name not in candidates:
                        candidates.append(name)
        if candidates:
            break

    if candidates:
        result["中标候选人名称"] = "、".join(candidates)

    return result


def parse_result_detail(url):
    resp = get(url)
    if not resp:
        return {}

    soup = BeautifulSoup(resp.text, "html.parser")
    content_el = soup.select_one("table.gycq-table td") or soup.select_one(".cs_xq_content")
    full_text = content_el.get_text() if content_el else soup.get_text()
    result = {"中标结果公示链接": url}

    winners = []
    p1 = r'中\s*标\s*人[：:]\s*([^\s　\n\r，。；]{2,40}(?:有限公司|集团|联合体|股份公司|建设有限公司|工程有限公司|咨询股份))'
    for m in re.finditer(p1, full_text):
        w = m.group(1).strip().rstrip("，。；　 ")
        if w and w not in winners:
            winners.append(w)

    p2 = r'(?:确定|确认)[^\n，。（(]{0,10}?([^\n，。（(【\s]{2,30}(?:有限公司|集团|联合体|股份公司|股份有限公司|建设有限公司|工程有限公司|咨询股份))(?=为中标人|中标)'
    for m in re.finditer(p2, full_text):
        w = m.group(1).strip()
        if w and w not in winners:
            winners.append(w)

    p3 = r'中标单位[：:]\s*([^\s　\n\r，。；]{2,40}(?:有限公司|集团|联合体|股份公司))'
    for m in re.finditer(p3, full_text):
        w = m.group(1).strip()
        if w and w not in winners:
            winners.append(w)

    p4 = r'中标人应为([^\s，。；\n]{2,40}(?:有限公司|集团|联合体|股份公司|股份有限公司|建设有限公司|工程有限公司|咨询股份))'
    for m in re.finditer(p4, full_text):
        w = m.group(1).strip()
        if w and w not in winners:
            winners.append(w)

    def clean_winner(w):
        w = re.split(r'中标价', w)[0]
        return w.strip().rstrip("，。；　 、")

    winners = [clean_winner(w) for w in winners]
    winners = [w for w in winners if len(w) >= 2]

    if winners:
        result["中标人"] = "、".join(winners)

    return result

# ==================== 搜索历史 ====================

def search_history(project_name, channel_id):
    return fetch_list(channel_id, days=4000, search_title=project_name, verbose=False)


def find_and_fill_history(project_name, location, from_step=2):
    print(f"  [历史] 搜索中...")
    row = empty_row(project_name, location)
    history_parts = []

    items = search_history(project_name, CHANNEL["招标计划"])
    if items:
        sleep()
        detail = parse_plan_detail(items[0]["link"])
        for k, v in detail.items():
            if k in COLUMNS and v:
                row[k] = v
        if items[0]["location"] != "暂无":
            row["交易地区"] = items[0]["location"]
        for i in items:
            history_parts.append(f"{i['raw_title']}|{i['link']}")
        print(f"    ✓ 招标计划")

    if from_step >= 3:
        items = search_history(project_name, CHANNEL["招标公告"])
        notice_items = [i for i in items if not should_skip(i["raw_title"]) and not is_amendment(i["raw_title"]) and not should_terminate(i["raw_title"])]
        if notice_items:
            sleep()
            detail = parse_notice_detail(notice_items[0]["link"])
            for k, v in detail.items():
                if k in COLUMNS and v:
                    row[k] = v
            if notice_items[0]["date"]:
                row["招标公告发布日期"] = notice_items[0]["date"]
            print(f"    ✓ 招标公告")
        for i in items:
            history_parts.append(f"{i['raw_title']}|{i['link']}")

    if from_step >= 4:
        items = search_history(project_name, CHANNEL["变更公告"])
        if items:
            row["变更公告链接"] = "；".join([i["link"] for i in items])
            for i in items:
                history_parts.append(f"{i['raw_title']}|{i['link']}")
            print(f"    ✓ 变更公告 {len(items)}条")

    if from_step >= 5:
        items = search_history(project_name, CHANNEL["中标候选人"])
        if items:
            sleep()
            detail = parse_candidate_detail(items[0]["link"])
            for k, v in detail.items():
                if k in COLUMNS and v:
                    row[k] = v
            for i in items:
                history_parts.append(f"{i['raw_title']}|{i['link']}")
            print(f"    ✓ 候选人")

    if from_step >= 6:
        items = search_history(project_name, CHANNEL["中标结果"])
        if items:
            sleep()
            detail = parse_result_detail(items[0]["link"])
            for k, v in detail.items():
                if k in COLUMNS and v:
                    row[k] = v
            for i in items:
                history_parts.append(f"{i['raw_title']}|{i['link']}")
            print(f"    ✓ 结果")

    if history_parts:
        row["公告历史"] = "；".join(history_parts)

    return row


def update_history(df, idx, new_items):
    existing = str(df.at[idx, "公告历史"])
    new_parts = [f"{i['raw_title']}|{i['link']}" for i in new_items]
    for part in new_parts:
        if existing == "暂无":
            existing = part
        elif part not in existing:
            existing = existing + "；" + part
    df.at[idx, "公告历史"] = existing
    return df

# ==================== CSV操作 ====================

def load_csv():
    if os.path.exists(CSV_PATH):
        df = pd.read_csv(CSV_PATH, dtype=str).fillna("暂无")
        for col in COLUMNS:
            if col not in df.columns:
                df[col] = "暂无"
        df = df[COLUMNS]
        BAD_KW = ["备案号", "晋ICP", "网站标识码", "行政审批服务管理局", "投诉举报热线"]
        def is_bad_qual(v):
            return any(kw in str(v) for kw in BAD_KW)
        bad_mask = df["投标人资格要求"].apply(is_bad_qual)
        if bad_mask.any():
            print(f"[CSV] 清除 {bad_mask.sum()} 行错误的投标人资格要求数据")
            df.loc[bad_mask, "投标人资格要求"] = "暂无"
        print(f"[CSV] 加载 {len(df)} 行")
    else:
        df = pd.DataFrame(columns=COLUMNS)
        print("[CSV] 新建")
    return df

def save_csv(df):
    df.to_csv(CSV_PATH, index=False, encoding="utf-8-sig")

def find_row(df, project_name):
    if df.empty:
        return -1
    mask = df["项目名称"] == project_name
    if mask.any():
        return df[mask].index[0]
    min_len = 5
    for idx, row in df.iterrows():
        name = str(row["项目名称"])
        if len(name) >= min_len and len(project_name) >= min_len:
            if project_name in name or name in project_name:
                return idx
    return -1

def update_row(df, idx, updates):
    for k, v in updates.items():
        if k not in df.columns or not v or v == "暂无":
            continue
        if k in ("变更公告链接", "终止公告链接"):
            existing = str(df.at[idx, k])
            if existing == "暂无":
                df.at[idx, k] = v
            elif v not in existing:
                df.at[idx, k] = existing + "；" + v
        else:
            df.at[idx, k] = v
    return df

def insert_row(df, row):
    return pd.concat([df, pd.DataFrame([row], columns=COLUMNS)], ignore_index=True)

# ==================== 五步爬取 ====================

def step1_plan(df):
    print("\n===== 第一步：招标计划 =====")
    items = fetch_list(CHANNEL["招标计划"], CRAWL_DAYS)
    for item in items:
        name = item["title"]
        print(f"\n  [{name}]")
        if find_row(df, name) >= 0:
            print("  → 已存在，跳过")
            continue
        sleep()
        detail = parse_plan_detail(item["link"])
        row = empty_row(name, item["location"])
        for k, v in detail.items():
            if k in COLUMNS and v:
                row[k] = v
        row["公告历史"] = f"{item['raw_title']}|{item['link']}"
        df = insert_row(df, row)
        print(f"  → 插入: 统一代码={row.get('统一代码','暂无')}, 预计={row.get('招标公告发布日期','暂无')}")
        save_csv(df)
    return df


def _handle_terminate(df, item):
    name = item["title"]
    idx = find_row(df, name)
    if idx >= 0:
        df = update_row(df, idx, {"终止公告链接": item["link"]})
        df = update_history(df, idx, [item])
        print(f"  → 更新终止公告链接")
    else:
        row = find_and_fill_history(name, item["location"], from_step=6)
        row["终止公告链接"] = item["link"]
        df = insert_row(df, row)
    return df

def _handle_amendment(df, item, from_step=2):
    name = item["title"]
    idx = find_row(df, name)
    if idx >= 0:
        df = update_row(df, idx, {"变更公告链接": item["link"]})
        df = update_history(df, idx, [item])
        print(f"  → 更新变更链接")
    else:
        row = find_and_fill_history(name, item["location"], from_step=from_step)
        row["变更公告链接"] = item["link"]
        df = insert_row(df, row)
    return df

def _handle_notice(df, item):
    name = item["title"]
    real_date = item["date"]
    sleep()
    detail = parse_notice_detail(item["link"])
    updates = {
        "招标公告发布日期": real_date,
        "招标文件获取时间": detail.get("招标文件获取时间", ""),
        "投标人资格要求": detail.get("投标人资格要求", ""),
        "招标公告链接": item["link"],
    }
    idx = find_row(df, name)
    if idx >= 0:
        print(f"  → 更新")
        df = update_row(df, idx, updates)
        df = update_history(df, idx, [item])
    else:
        row = find_and_fill_history(name, item["location"], from_step=2)
        for k, v in updates.items():
            if v:
                row[k] = v
        df = insert_row(df, row)
    return df

def _handle_candidate(df, item, from_step=4):
    name = item["title"]
    sleep()
    detail = parse_candidate_detail(item["link"])
    updates = {
        "中标候选人名称": detail.get("中标候选人名称", ""),
        "中标候选人公示链接": item["link"],
    }
    idx = find_row(df, name)
    if idx >= 0:
        print(f"  → 更新候选人: {updates.get('中标候选人名称','')[:30]}")
        df = update_row(df, idx, updates)
        df = update_history(df, idx, [item])
    else:
        row = find_and_fill_history(name, item["location"], from_step=from_step)
        for k, v in updates.items():
            if v:
                row[k] = v
        df = insert_row(df, row)
    return df

def _handle_result(df, item, from_step=5):
    name = item["title"]
    sleep()
    detail = parse_result_detail(item["link"])
    updates = {
        "中标人": detail.get("中标人", ""),
        "中标结果公示链接": item["link"],
    }
    idx = find_row(df, name)
    if idx >= 0:
        print(f"  → 更新中标人: {updates.get('中标人','')[:30]}")
        df = update_row(df, idx, updates)
        df = update_history(df, idx, [item])
    else:
        row = find_and_fill_history(name, item["location"], from_step=from_step)
        for k, v in updates.items():
            if v:
                row[k] = v
        df = insert_row(df, row)
    return df


def step2_notice(df):
    print("\n===== 第二步：招标/资质公告 =====")
    items = fetch_list(CHANNEL["招标公告"], CRAWL_DAYS)
    for item in items:
        raw = item["raw_title"]
        name = item["title"]
        type_ = classify_title(raw)
        if type_ == "skip":
            print(f"\n  [跳过-控制价] {raw}")
            continue
        elif type_ == "terminate":
            print(f"\n  [终止公告] {name}")
            df = _handle_terminate(df, item)
        elif type_ == "amendment":
            print(f"\n  [变更公告-来自招标频道] {name}")
            df = _handle_amendment(df, item, from_step=2)
        elif type_ == "candidate":
            print(f"\n  [候选人-来自招标频道] {name}")
            df = _handle_candidate(df, item, from_step=4)
        elif type_ == "result":
            print(f"\n  [中标结果-来自招标频道] {name}")
            df = _handle_result(df, item, from_step=5)
        else:
            print(f"\n  [{name}] 日期={item['date']}")
            df = _handle_notice(df, item)
        save_csv(df)
    return df


def step3_amendment(df):
    print("\n===== 第三步：变更公告 =====")
    items = fetch_list(CHANNEL["变更公告"], CRAWL_DAYS)
    for item in items:
        raw = item["raw_title"]
        name = item["title"]
        type_ = classify_title(raw)
        print(f"\n  [{name}]")
        if type_ == "skip":
            print(f"  [跳过-控制价] {raw}")
            continue
        elif type_ == "terminate":
            df = _handle_terminate(df, item)
        elif type_ == "notice":
            df = _handle_notice(df, item)
        elif type_ == "candidate":
            df = _handle_candidate(df, item, from_step=4)
        elif type_ == "result":
            df = _handle_result(df, item, from_step=5)
        else:
            df = _handle_amendment(df, item, from_step=3)
        save_csv(df)
    return df


def step4_candidate(df):
    print("\n===== 第四步：中标候选人公示 =====")
    items = fetch_list(CHANNEL["中标候选人"], CRAWL_DAYS)
    for item in items:
        raw = item["raw_title"]
        name = item["title"]
        type_ = classify_title(raw)
        print(f"\n  [{name}]")
        if type_ == "skip":
            print(f"  [跳过-控制价] {raw}")
            continue
        elif type_ == "terminate":
            df = _handle_terminate(df, item)
        elif type_ == "amendment":
            df = _handle_amendment(df, item, from_step=3)
        elif type_ == "notice":
            df = _handle_notice(df, item)
        elif type_ == "result":
            df = _handle_result(df, item, from_step=5)
        else:
            df = _handle_candidate(df, item, from_step=4)
        save_csv(df)
    return df


def step5_result(df):
    print("\n===== 第五步：中标结果公示 =====")
    items = fetch_list(CHANNEL["中标结果"], CRAWL_DAYS)
    for item in items:
        raw = item["raw_title"]
        name = item["title"]
        type_ = classify_title(raw)
        print(f"\n  [{name}]")
        if type_ == "skip":
            print(f"  [跳过-控制价] {raw}")
            continue
        elif type_ == "terminate":
            df = _handle_terminate(df, item)
        elif type_ == "amendment":
            df = _handle_amendment(df, item, from_step=3)
        elif type_ == "notice":
            df = _handle_notice(df, item)
        elif type_ == "candidate":
            df = _handle_candidate(df, item, from_step=4)
        else:
            df = _handle_result(df, item, from_step=5)
        save_csv(df)
    return df

# ==================== 文本索引 + 自检日志 ====================

def build_index(df):
    path = CSV_PATH.replace(".csv", "_index.txt")
    with open(path, "w", encoding="utf-8") as f:
        f.write(f"山西省公共资源交易平台 文本索引\n")
        f.write(f"生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
        f.write(f"总记录数: {len(df)}\n")
        f.write("=" * 60 + "\n\n")
        for idx, row in df.iterrows():
            f.write(f"[{idx+1}] {row['项目名称']}\n")
            f.write(f"  地区: {row['交易地区']} | 招标人: {row['招标人名称']}\n")
            f.write(f"  发布日期: {row['招标公告发布日期']} | 获取时间: {row['招标文件获取时间']}\n")
            f.write(f"  中标人: {row['中标人']}\n")
            f.write(f"  公告链接: {row['招标公告链接']}\n\n")
    print(f"[索引] → {path}")


def build_check_log(df, skipped_unknown):
    """生成自检日志，记录两类问题"""
    with open(CHECK_LOG_PATH, "w", encoding="utf-8") as f:
        f.write(f"山西省公共资源交易平台 自检日志\n")
        f.write(f"生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
        f.write("=" * 60 + "\n\n")

        f.write(f"【类型1】未识别公告（共{len(skipped_unknown)}条）\n")
        f.write("-" * 40 + "\n")
        if skipped_unknown:
            for item in skipped_unknown:
                f.write(f"  标题: {item['raw_title']}\n")
                f.write(f"  链接: {item['link']}\n\n")
        else:
            f.write("  无\n\n")

        f.write(f"【类型2】有链接但字段提取失败\n")
        f.write("-" * 40 + "\n")
        problems = []
        for idx, row in df.iterrows():
            issues = []
            if row["招标公告链接"] != "暂无":
                if row["投标人资格要求"] == "暂无":
                    issues.append("投标人资格要求未提取")
                if row["招标文件获取时间"] == "暂无":
                    issues.append("招标文件获取时间未提取")
            if row["中标候选人公示链接"] != "暂无" and row["中标候选人名称"] == "暂无":
                issues.append("中标候选人名称未提取")
            if row["中标结果公示链接"] != "暂无" and row["中标人"] == "暂无":
                issues.append("中标人未提取")
            if issues:
                problems.append((row["项目名称"], issues))

        if problems:
            for name, issues in problems:
                f.write(f"  项目: {name}\n")
                for issue in issues:
                    f.write(f"    - {issue}\n")
                f.write("\n")
        else:
            f.write("  无\n\n")

    print(f"[自检] → {CHECK_LOG_PATH}")

# ==================== 主程序 ====================

def main():
    print("=" * 60)
    print("山西省公共资源交易平台爬虫 v3.4")
    print(f"时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"输出: {CSV_PATH}")
    print("=" * 60)

    print("初始化Session...")
    try:
        SESSION.get("https://prec.sxzwfw.gov.cn/jyxx/index.jhtml", headers=HEADERS, timeout=15)
        print("Session初始化成功")
    except Exception as e:
        print(f"Session初始化失败: {e}")

    # ── 正常五步爬取 ──
    df = load_csv()
    df = step1_plan(df)
    df = step2_notice(df)
    df = step3_amendment(df)
    df = step4_candidate(df)
    df = step5_result(df)
    save_csv(df)
    build_index(df)

    # ── 生成自检日志 ──
    build_check_log(df, SKIPPED_UNKNOWN)

    # ── AI类型1：未识别公告智能分类 → 正则处理 → 写CSV ──
    df = ai_classify_and_handle(df)
    save_csv(df)

    # ── 重新生成自检日志（包含类型1处理后新产生的字段缺失）──
    # 类型1已处理完，skipped_unknown 传空列表，只更新类型2部分
    build_check_log(df, [])

    # ── AI类型2：字段缺失兜底补全 → 重爬 → 补字段 → 写CSV → 写AI报告 ──
    df = ai_supplement(df)
    save_csv(df)

    print(f"\n{'='*60}")
    print(f"完成！共 {len(df)} 条记录 → {CSV_PATH}")
    print(f"自检日志  → {CHECK_LOG_PATH}")
    print(f"AI报告    → {AI_REPORT_PATH}")
    print("=" * 60)


if __name__ == "__main__":
    main()