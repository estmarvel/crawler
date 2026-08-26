import requests
import json

headers = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Accept': 'application/json, text/plain, */*',
    'Referer': 'http://shanxi.fzbidding.com/bidinfo',
    'Origin': 'http://shanxi.fzbidding.com'
}

base = 'http://shanxi.fzbidding.com:8001'
list_url = base + '/hz/portal/portalBidding/list'
detail_url = base + '/hz/portal/portalBidding/detail'

# 1. 先获取列表中的一条数据
print('=== 列表数据 ===')
r = requests.post(list_url, json={'page': 1, 'limit': 3}, headers=headers, timeout=10)
data = r.json()
record = data['records'][0]
print(f'ID: {record["id"]}')
print(f'标题: {record["noticeName"]}')
print(f'noticeContext长度: {len(record.get("noticeContext", "") or "")}')
print(f'noticeContext前500字:')
print((record.get('noticeContext') or '')[:500])

# 2. 获取详情页数据进行对比
print('\n\n=== 详情页数据 ===')
r2 = requests.post(detail_url, json={'id': record['id']}, headers=headers, timeout=10)
detail_data = r2.json()
detail = detail_data['detail']
print(f'ID: {detail["id"]}')
print(f'标题: {detail["noticeName"]}')
print(f'noticeContext长度: {len(detail.get("noticeContext", "") or "")}')
print(f'noticeContext前500字:')
print((detail.get('noticeContext') or '')[:500])

# 3. 对比字段差异
print('\n\n=== 字段对比 ===')
list_keys = set(record.keys())
detail_keys = set(detail.keys())
print(f'列表独有字段: {list_keys - detail_keys}')
print(f'详情独有字段: {detail_keys - list_keys}')

# 4. 检查列表中noticeContext是否完整
print('\n\n=== 内容完整性检查 ===')
list_ctx = record.get('noticeContext') or ''
detail_ctx = detail.get('noticeContext') or ''
print(f'列表内容长度: {len(list_ctx)}')
print(f'详情内容长度: {len(detail_ctx)}')
print(f'内容是否相同: {list_ctx == detail_ctx}')

# 5. 看看还有什么其他参数可以过滤
print('\n\n=== 测试其他过滤参数 ===')
# 测试portalBiddingType参数
for pbt in ['0', '1', '2', '3', '4']:
    r = requests.post(list_url, json={'page': 1, 'limit': 5, 'portalBiddingType': pbt}, headers=headers, timeout=10)
    data = r.json()
    print(f'portalBiddingType={pbt}: total={data.get("total", 0)}, records={len(data.get("records", []))}')

# 测试tenderProjectType参数
print('\n测试项目类型:')
for tpt in ['工程', '货物', '服务']:
    r = requests.post(list_url, json={'page': 1, 'limit': 5, 'tenderProjectType': tpt}, headers=headers, timeout=10)
    data = r.json()
    print(f'tenderProjectType={tpt}: total={data.get("total", 0)}, records={len(data.get("records", []))}')
