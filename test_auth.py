from zhipuai import ZhipuAI
import inspect
client = ZhipuAI(api_key="123")
print(inspect.getsource(client.file_parser.create))
