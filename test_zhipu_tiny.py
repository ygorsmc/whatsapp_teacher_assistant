from zhipuai import ZhipuAI
client = ZhipuAI(api_key="3db79adfc6ce435e899b8b193ff8ee7f.xL6qLwssoGgYY3ZP")
try:
    with open("tiny.pdf", "rb") as f:
        response = client.file_parser.create(file=f, tool_type="zhipu-pro", file_type="pdf")
    print(response.model_dump())
except Exception as e:
    print("Error:", e)
