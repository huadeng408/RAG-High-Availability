import openai


# ✅ 正确示例：
openai.api_key = "sk-fd9da429b629cf4f2a495ba1f6bc6995cd9e4a6518ed9f150f12d609d8286f74"

# 测试密钥是否有效
try:
    response = openai.chat.completions.create(
        model="gpt-3.5-turbo",
        messages=[{"role": "user", "content": "测试"}]
    )
    print("密钥有效！响应：", response.choices[0].message.content)
except openai.AuthenticationError as e:
    print("密钥仍无效：", e)
except Exception as e:
    print("其他错误：", e)