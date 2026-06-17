#!/usr/bin/env python3
"""
从 全文文本 字段中重新提取空值字段，修正爬虫结果 JSON 文件。

处理文件：
  - gczb_招标资审公告.json
  - gchxr_中标候选人公示.json
  - gcgs_中标结果公示.json

策略：对每个空字段，使用多层正则 fallback 从 全文文本 中提取。
"""

import json
import re
import os
import sys

# ── 工具函数 ──────────────────────────────────────────────

def clean_span_spaces(text):
    """
    清理 HTML span 标签导致的碎片化空格。
    "SXXZY-GCG K -2 6 0 14" → "SXXZY-GCGK-26014"
    策略：字母数字之间的单空格合并，保留中文/标点周围的空格。
    """
    if not text:
        return text
    # 合并被空格拆散的数字/字母序列（连续三次可处理深层拆分）
    for _ in range(5):
        new_text = re.sub(r'([A-Za-z0-9]) ([A-Za-z0-9])', r'\1\2', text)
        if new_text == text:
            break
        text = new_text
    # 合并数字与中文顿号之间的空格
    text = re.sub(r'(\d) +([）\)\]])', r'\1\2', text)
    text = re.sub(r'([（\(\[。，、]) +(\d|[A-Z])', r'\1\2', text)
    return text


def clean_extracted(val):
    """清理提取值：去首尾空白、去尾随标点、跳过纯标点"""
    if not val:
        return ""
    val = val.strip()
    # 去尾随的冒号、逗号、顿号
    val = re.sub(r'[：:，,、。\s]+$', '', val)
    # 去前导的冒号、空格
    val = re.sub(r'^[：:，,、\s]+', '', val)
    # 纯标点/空白 → 空
    if re.match(r'^[\s：:，,、。；;!！?？\'"\"''""\[\]【】()（）\-—…\.·]+$', val):
        return ""
    return val


def safe_truncate(text, max_len=500):
    """截断文本避免正则回溯爆炸"""
    if not text:
        return ""
    return text[:max_len]


# ── 字段提取函数 ────────────────────────────────────────────

def extract_title(bt):
    """
    从全文文本提取项目名称（通用）。
    多层 fallback：
      1. 发布时间后到括号/一、前的文本
      2. 项目名称：XXX
      3. 发布时间后，提取到 一、/招标/项目 前的较长文本
      4. 从正文中找 工程名称 描述符后的文本
    """
    # Clean HTML comments from body text
    bt = re.sub(r'<!--.*?-->|-->\s*', '', bt)

    # 1. 发布时间 + 空格 + 标题 ... 直到遇到 ( （ 招标 标段 项目编号
    m = re.search(
        r'(?:公告|公示)发布时间：\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2}\s+'
        r'(.+?)(?=\s*[（(]\s*(?:招标|标段|项目|工程))\s*',
        bt
    )
    if m:
        val = m.group(1).strip()
        # 排除以 "一、" "二、" 开头的（公告内容以段落开头）
        if not re.match(r'^[一二三四五六七八九十]、', val):
            # 去掉数字前缀如 "11 "（来自HTML编码问题）
            val = re.sub(r'^\d{1,3}\s+', '', val)
            val = clean_extracted(val)
            if val and len(val) >= 4:
                return _post_clean_title(val)

    # 2. 项目名称：XXX / 工程名称：XXX
    m = re.search(r'(?:项目名称|工程名称|招标项目名称)\s*[：:]\s*(.+?)(?:\s*(?:一、|二、|\S*[编代]号|招标方|招标人|开标|中标|$))', bt)
    if m:
        val = clean_extracted(m.group(1))
        if val and len(val) >= 4:
            return _post_clean_title(val)

    # 3. 发布时间后到一、/招标/本/经 前的文本（更宽松的边界）
    m = re.search(
        r'(?:公告|公示)发布时间：\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2}\s+'
        r'(.+?)(?=\s*(?:一、|二、|\n\n|（|\(|招标|\s*本\S+经|\s*经评标))\s*',
        bt
    )
    if m:
        val = m.group(1).strip()
        val = re.sub(r'^\d{1,3}\s+', '', val)
        val = clean_extracted(val)
        if val and len(val) >= 4:
            return _post_clean_title(val)

    # 4. 从 招标编号） 之后寻找正文中的实际项目名称
    # 例: "... (招标编号：XM2026G004) 公示开始时间：... 寺头乡五村六条农村道路提质改造工程，经评标委员会评审..."
    m = re.search(r'[）)]\s*公示(?:开始|结束)时间[^，。\n]{5,80}(?:[，。\s]|$)\s*([\u4e00-\u9fff]{4,80}?)(?:[，。]|\s*经评标|\s*，经|\s*本\S+经|\s*现公示)', bt)
    if m:
        val = clean_extracted(m.group(1))
        val = re.sub(r'^\d{1,3}\s+', '', val)
        if val and len(val) >= 4 and '公示' not in val and '时间' not in val and '评标' not in val:
            return _post_clean_title(val)

    return ""


def _post_clean_title(val):
    """Post-process extracted title: strip date markers and trailing noise."""
    if not val:
        return val
    # If title contains "公示开始时间" or similar date markers, extract the actual project name after them
    # Handle cases like "公示开始时间：2026-06-17 15:00:00 公示结束时间：2026-06-20 15:00:00 寺头乡..."
    # Match date values: "YYYY年MM月DD日" or "YYYY-MM-DD" or "YYYY-MM-DD HH:MM:SS"
    m = re.search(
        r'(?:公示(?:开始|结束)时间[：:]\s*'
        r'(?:\d[\d\s\-]*年\s*\d[\d\s]*月\s*\d[\d\s]*日'
        r'(?:\s*\d[\d\s]*时\s*\d[\d\s]*分)?'
        r'|\d{4}-\d{2}-\d{2}(?:\s+\d{2}:\d{2}(?::\d{2})?)?'
        r')\s*)+'
        r'([\u4e00-\u9fff][\u4e00-\u9fff\s（）()\-0-9a-zA-Z]{4,})$',
        val
    )
    if m:
        val = m.group(1).strip()
    # Strip trailing "公示日期：..." noise
    val = re.sub(r'\s*公示日期[：:].*$', '', val)
    # Strip trailing " 一、内容 ..." noise
    val = re.sub(r'\s*一、\s*内容.*$', '', val)
    # Strip " 项目编号：... " 
    val = re.sub(r'\s*项目编号[：:]\s*\S+\s*', '', val)
    # Strip " 项目名称：... " that got stuck
    val = re.sub(r'\s*项目名称[：:]\s*\S.*$', '', val)
    return clean_extracted(val)


def extract_zbbh(bt):
    """
    从全文文本提取招标编号/项目编号（通用）。
    """
    # 1. （招标编号：XXX）/ (招标编号：XXX) / （招标编号 ： XXX）
    m = re.search(r'[（(]\s*招标编号\s*[：:]\s*(.{1,100}?)\s*[）)]', bt)
    if m:
        val = clean_span_spaces(m.group(1).strip())
        val = clean_extracted(val)
        if val and len(val) >= 5:
            return val

    # 2. 无括号：招标编号：XXX
    m = re.search(r'招标编号\s*[：:]\s*(.{1,80}?)(?=\s*[）)]|\s*一、|\s*二、|\s*招标|\s*项目|\s*本|\s*标段|\s*公示|\s*$)', bt)
    if m:
        val = clean_span_spaces(m.group(1).strip())
        val = clean_extracted(val)
        if val and len(val) >= 5:
            return val

    # 3. 项目编号：XXX
    m = re.search(r'项目编号\s*[：:]\s*(.{1,80}?)(?=\s*[）)]|\s*一、|\s*二、|\s*招标|\s*项目|\s*$)', bt)
    if m:
        val = clean_span_spaces(m.group(1).strip())
        val = clean_extracted(val)
        if val and len(val) >= 5:
            return val

    return ""


def extract_area(bt):
    """从全文文本提取招标项目所在地区（gczb 专用）。"""
    m = re.search(
        r'招标项目所在地区\s*[：:]\s*'
        r'(.+?)'
        r'(?=\s*(?:一、|二、|\d+\s*[、，,]|\n\s*\n|\s*招标条件|\s*项目规模|\s*项目概况|\S*编号|\s*$))',
        bt
    )
    if m:
        val = m.group(1).strip()
        # 清理尾部碎片（如 "1 、 招标条件 本"）
        val = re.sub(r'\s*\d+\s*[、，,]\s*招标条件.*$', '', val)
        val = clean_span_spaces(val)
        val = clean_extracted(val)
        if val and len(val) >= 2:
            return val
    return ""


def extract_gssj_start(bt):
    """从全文文本提取公示开始时间（gchxr 专用）。"""
    # Pre-clean span-fragmented digits/dates in body text
    bt = clean_span_spaces(bt)

    # 1. 公示开始时间：XXX
    m = re.search(r'公示开始时间\s*[：:]\s*(.{1,60}?)(?=\s*公示结束|\s*本|\s*一、|\s*经评标|\s*$)', bt)
    if m:
        val = m.group(1).strip()
        val = clean_extracted(val)
        if val:
            return val

    # 2. 公示日期：YYYY年MM月DD日 - YYYY年MM月DD日（组合格式，取前半）
    m = re.search(r'公示日期\s*[：:]\s*(\d{4}\s*年\s*\d{1,2}\s*月\s*\d{1,2}\s*日)', bt)
    if m:
        val = m.group(1).strip()
        if val:
            return val

    # 3. 公示期限：YYYY年MM月DD日 HH时MM分 至 ...（取"至"前部分）
    m = re.search(r'公示期限\s*[：:]\s*(\d{4}\s*年\s*\d{1,2}\s*月\s*\d{1,2}\s*日\s*\d{1,2}\s*时\s*\d{1,2}\s*分)', bt)
    if m:
        val = m.group(1).strip()
        if val:
            return val

    return ""


def extract_gssj_end(bt):
    """从全文文本提取公示结束时间（gchxr 专用）。"""
    # Pre-clean span-fragmented digits/dates in body text
    bt = clean_span_spaces(bt)

    # 1. 公示结束时间：XXX
    m = re.search(r'公示结束时间\s*[：:]\s*(.{1,60}?)(?=\s*本|\s*一、|\s*经评标|\s*$)', bt)
    if m:
        val = m.group(1).strip()
        val = clean_extracted(val)
        if val:
            return val

    # 2. 公示日期：YYYY年MM月DD日 - YYYY年MM月DD日（组合格式，取后半）
    m = re.search(r'公示日期\s*[：:]\s*\d{4}\s*年\s*\d{1,2}\s*月\s*\d{1,2}\s*日\s*[-－—至]\s*(\d{4}\s*年\s*\d{1,2}\s*月\s*\d{1,2}\s*日)', bt)
    if m:
        val = m.group(1).strip()
        if val:
            return val

    # 3. 公示期限：YYYY年MM月DD日 HH时MM分 至 YYYY年MM月DD日 HH时MM分（取"至"后部分）
    m = re.search(r'公示期限\s*[：:]\s*\d{4}\s*年\s*\d{1,2}\s*月\s*\d{1,2}\s*日\s*\d{1,2}\s*时\s*\d{1,2}\s*分\s*至\s*(\d{4}\s*年\s*\d{1,2}\s*月\s*\d{1,2}\s*日\s*\d{1,2}\s*时\s*\d{1,2}\s*分)', bt)
    if m:
        val = m.group(1).strip()
        if val:
            return val

    return ""


def extract_zbr(bt):
    """从全文文本提取中标人（gcgs 专用）。"""
    # 1. 中标人: XXX / 中标单位: XXX
    m = re.search(r'(?:中标人|中标单位)\s*[：:]\s*(.+?)(?:\s*[\n\r]|\s*$)',
                  bt[:max(bt.find('招标人或其'), 4000) if bt.find('招标人或其') > 0 else 4000])
    if m:
        val = m.group(1).strip()
        val = clean_span_spaces(val)
        val = clean_extracted(val)
        if val and len(val) >= 2:
            return val

    # 2. 选定/确定XXX为该项目的中标人
    m = re.search(r'(?:选定|确定)(.+?)为(?:该|本)项目的中标人', bt)
    if m:
        val = m.group(1).strip()
        val = clean_span_spaces(val)
        val = clean_extracted(val)
        if val and len(val) >= 2:
            return val

    return ""


# ── 各栏目处理 ──────────────────────────────────────────────

def fix_gczb(filepath):
    """修复招标资审公告的空字段"""
    with open(filepath, 'r', encoding='utf-8') as f:
        data = json.load(f)

    fixed_count = 0
    for item in data['data']:
        bt = item.get('全文文本', '')
        if not bt:
            continue

        changes = []

        # 项目名称
        if not item.get('项目名称'):
            val = extract_title(bt)
            if val:
                item['项目名称'] = val
                changes.append(f'项目名称={val[:60]}')

        # 招标编号
        if not item.get('招标编号'):
            val = extract_zbbh(bt)
            if val:
                item['招标编号'] = val
                changes.append(f'招标编号={val[:40]}')

        # 招标项目所在地区
        if not item.get('招标项目所在地区'):
            val = extract_area(bt)
            if val:
                item['招标项目所在地区'] = val
                changes.append(f'地区={val[:40]}')

        if changes:
            fixed_count += 1
            print(f"  [gczb] ID={item['详情ID']}: {', '.join(changes)}")

    # 保存
    outpath = filepath.replace('.json', '_fixed.json')
    with open(outpath, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(f"  gczb: 修复 {fixed_count} 条，保存至 {outpath}")
    return fixed_count


def fix_gchxr(filepath):
    """修复中标候选人公示的空字段"""
    with open(filepath, 'r', encoding='utf-8') as f:
        data = json.load(f)

    fixed_count = 0
    for item in data['data']:
        bt = item.get('全文文本', '')
        if not bt:
            continue

        changes = []

        # 项目名称
        if not item.get('项目名称'):
            val = extract_title(bt)
            if val:
                item['项目名称'] = val
                changes.append(f'项目名称={val[:60]}')

        # 招标编号
        if not item.get('招标编号'):
            val = extract_zbbh(bt)
            if val:
                item['招标编号'] = val
                changes.append(f'招标编号={val[:40]}')

        # 公示开始时间
        if not item.get('公示开始时间'):
            val = extract_gssj_start(bt)
            if val:
                item['公示开始时间'] = val
                changes.append(f'开始={val[:30]}')

        # 公示结束时间
        if not item.get('公示结束时间'):
            val = extract_gssj_end(bt)
            if val:
                item['公示结束时间'] = val
                changes.append(f'结束={val[:30]}')

        if changes:
            fixed_count += 1
            print(f"  [gchxr] ID={item['详情ID']}: {', '.join(changes)}")

    outpath = filepath.replace('.json', '_fixed.json')
    with open(outpath, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(f"  gchxr: 修复 {fixed_count} 条，保存至 {outpath}")
    return fixed_count


def fix_gcgs(filepath):
    """修复中标结果公示的空字段"""
    with open(filepath, 'r', encoding='utf-8') as f:
        data = json.load(f)

    fixed_count = 0
    for item in data['data']:
        bt = item.get('全文文本', '')
        if not bt:
            continue

        changes = []

        # 项目名称
        if not item.get('项目名称'):
            val = extract_title(bt)
            if val:
                item['项目名称'] = val
                changes.append(f'项目名称={val[:60]}')

        # 招标编号
        if not item.get('招标编号'):
            val = extract_zbbh(bt)
            if val:
                item['招标编号'] = val
                changes.append(f'招标编号={val[:40]}')

        # 中标人
        if not item.get('中标人'):
            val = extract_zbr(bt)
            if val:
                item['中标人'] = val
                changes.append(f'中标人={val[:40]}')

        if changes:
            fixed_count += 1
            print(f"  [gcgs] ID={item['详情ID']}: {', '.join(changes)}")

    outpath = filepath.replace('.json', '_fixed.json')
    with open(outpath, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(f"  gcgs: 修复 {fixed_count} 条，保存至 {outpath}")
    return fixed_count


# ── 统计报告 ──────────────────────────────────────────────────

def print_summary(base_dir, files):
    """打印修复前后对比"""
    print("\n" + "=" * 60)
    print("修复前后空字段对比")
    print("=" * 60)

    for fname in files:
        orig_path = os.path.join(base_dir, fname)
        fixed_path = os.path.join(base_dir, fname.replace('.json', '_fixed.json'))

        if not os.path.exists(orig_path):
            continue

        with open(orig_path, 'r') as f:
            orig = json.load(f)

        fixed_exists = os.path.exists(fixed_path)
        if fixed_exists:
            with open(fixed_path, 'r') as f:
                fixed = json.load(f)

        # 统计原始空字段
        fields = set()
        for item in orig['data']:
            for k, v in item.items():
                if isinstance(v, str) and not v.strip() and k not in ('全文文本',):
                    fields.add(k)

        print(f"\n{fname}:")
        for field in sorted(fields):
            orig_empty = sum(1 for i in orig['data'] if not i.get(field, '').strip())
            orig_total = len(orig['data'])
            if fixed_exists:
                fixed_empty = sum(1 for i in fixed['data'] if not i.get(field, '').strip())
                impr = orig_empty - fixed_empty
                print(f"  {field}: {orig_empty}/{orig_total} → {fixed_empty}/{orig_total} (修复 {impr} 条)")
            else:
                print(f"  {field}: {orig_empty}/{orig_total}")


# ── 主流程 ────────────────────────────────────────────────────

def main():
    base_dir = os.path.dirname(os.path.abspath(__file__))
    files = [
        'gczb_招标资审公告.json',
        'gchxr_中标候选人公示.json',
        'gcgs_中标结果公示.json',
    ]

    total = 0
    for fname in files:
        path = os.path.join(base_dir, fname)
        if not os.path.exists(path):
            print(f"文件不存在: {path}")
            continue
        print(f"\n处理: {fname}")
        if 'gczb' in fname:
            total += fix_gczb(path)
        elif 'gchxr' in fname:
            total += fix_gchxr(path)
        elif 'gcgs' in fname:
            total += fix_gcgs(path)

    print(f"\n总计修复: {total} 条记录")
    print_summary(base_dir, files)


if __name__ == '__main__':
    main()
