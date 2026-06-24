from openai import OpenAI

client = OpenAI(
    api_key="sk-b5WCpX7UhAjiFXrc6BjrttdAmNgkNPVO2K8aDPM51gvfHVtr", # 替换为使用key 
    base_url="https://vip.dmxapi.com/v1"  # 重要，非代理，不需要使用🪜
)

chat_completion = client.chat.completions.create(
    messages=[{"role": "user","content": "周树人和鲁迅是兄弟吗？",}],
    model="gpt-4o-mini" # 替换为模型名称，参考下面的模型列表
)
print(chat_completion)