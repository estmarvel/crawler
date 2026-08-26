import re

# 读取app.js
with open('app.js', 'r', encoding='utf-8') as f:
    js_content = f.read()

# 搜索 portalBidding/list 附近的代码
pattern = r'portalBidding/list[^}]{0,500}'
matches = re.findall(pattern, js_content, re.IGNORECASE)
print('=== portalBidding/list 附近代码 ===')
for i, m in enumerate(matches):
    print(f'\n--- Match {i+1} ---')
    print(m[:600])

# 搜索列表页相关的代码
print('\n\n=== 搜索列表页方法名 ===')
list_methods = re.findall(r'(getList|fetchList|loadList|queryList|searchList|getBiddingList)[^)]{0,200}', js_content)
for m in list_methods[:10]:
    print(m[:300])

# 搜索请求参数对象
print('\n\n=== 搜索请求参数 ===')
param_patterns = re.findall(r'params\s*[:=]\s*\{[^}]{0,300}\}', js_content)
for i, p in enumerate(param_patterns[:20]):
    if 'page' in p.lower() or 'size' in p.lower():
        print(f'\n--- Params {i+1} ---')
        print(p[:400])

# 搜索 portalBiddingType 或类型参数
print('\n\n=== 搜索类型参数 ===')
type_patterns = re.findall(r'portalBiddingType["\']?\s*[:=]\s*["\']?([^"\',}\s]+)', js_content)
print('portalBiddingType values:', list(set(type_patterns))[:20])

# 搜索完整的API调用
print('\n\n=== 搜索完整的API调用 ===')
api_calls = re.findall(r'\.post\(\s*["\']([^"\']*portalBidding[^"\']*)["\'][^)]{0,500}\)', js_content)
for i, call in enumerate(api_calls[:10]):
    print(f'\n--- API Call {i+1} ---')
    print(call[:500])
