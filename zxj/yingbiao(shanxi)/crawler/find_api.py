import requests
import re

headers = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
}

base = 'http://shanxi.fzbidding.com'

# 下载app.js
app_js = requests.get(base + '/js/app.e4550105.js', headers=headers)
print('app.js size:', len(app_js.text))

# 保存到文件以便分析
with open('app.js', 'w', encoding='utf-8') as f:
    f.write(app_js.text)

# 搜索API相关的模式
patterns = [
    r'baseURL["\']?\s*[:=]\s*["\']([^"\']+)["\']',
    r'["\'](/api[^"\']*)["\']',
    r'["\'](/gateway[^"\']*)["\']',
    r'url:\s*["\']([^"\']+)["\']',
    r'axios\.(get|post|put|delete)\(\s*["\']([^"\']+)["\']',
    r'\.request\(\{\s*url:\s*["\']([^"\']+)["\']',
]

print('\n=== API Patterns ===')
for pattern in patterns:
    matches = re.findall(pattern, app_js.text, re.IGNORECASE)
    if matches:
        print(f'\nPattern: {pattern}')
        unique_matches = list(set(matches))[:30]
        for m in unique_matches:
            print(f'  {m}')

# 搜索列表和详情相关的接口
print('\n=== 列表/详情相关API ===')
bid_patterns = re.findall(r'["\']([^"\']*(?:bid|notice|list|detail|info)[^"\']*)["\']', app_js.text, re.IGNORECASE)
unique_bids = list(set([p for p in bid_patterns if len(p) > 5 and '/' in p]))[:50]
for b in unique_bids:
    print(f'  {b}')
