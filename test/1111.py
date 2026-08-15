from openai import OpenAI

client = OpenAI(
    # 直接把密钥字符串写在这里
    api_key="sk-ws-H.RXIDMYD.xF6Y.MEQCIDpcOjKPJybMdfMNB4ylGrM6TaltK5gcoKBljp6m024OAiA8oVcWxwqBHoAQm-5b-QKyUj-rRNuD-lccphfzzFsF-w",
    base_url="https://llm-1st51c29issrwsrb.cn-beijing.maas.aliyuncs.com/compatible-mode/v1",
)

messages = [{"role": "user", "content": "你是谁"}]
completion = client.chat.completions.create(
    model="qwen3.7-plus",
    messages=messages,
    extra_body={"enable_thinking": True},
    stream=True
)

is_answering = False
print("\n" + "=" * 20 + " 思考过程 " + "=" * 20)
for chunk in completion:
    delta = chunk.choices[0].delta
    if hasattr(delta, "reasoning_content") and delta.reasoning_content is not None:
        if not is_answering:
            print(delta.reasoning_content, end="", flush=True)
    if hasattr(delta, "content") and delta.content:
        if not is_answering:
            print("\n" + "=" * 20 + " 完整回复 " + "=" * 20)
            is_answering = True
        print(delta.content, end="", flush=True)