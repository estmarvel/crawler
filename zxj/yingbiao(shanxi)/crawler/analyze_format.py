import json
import re
from bs4 import BeautifulSoup

def clean_html(html):
    if not html:
        return ''
    soup = BeautifulSoup(html, 'html.parser')
    text = soup.get_text(separator='\n')
    lines = [line.strip() for line in text.split('\n') if line.strip()]
    return '\n'.join(lines)

with open(r'd:\TRAE coding\招标信息爬取结果.json', 'r', encoding='utf-8') as f:
    old_result = json.load(f)

# 找几条不同类型的公告，打印正文前800字
count = 0
for notice_type, items in old_result.items():
    if not items:
        continue
    for item in items[:2]:
        html = item.get('原始HTML', '')
        text = clean_html(html)
        print(f'\n{"="*80}')
        print(f'类型: {notice_type}')
        print(f'标题: {item.get("公告标题", "")}')
        print(f'ID: {item.get("id", "")}')
        print(f'正文前800字:')
        print(text[:800])
        count += 1
    if count >= 8:
        break
