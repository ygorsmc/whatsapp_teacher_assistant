from zhipuai import ZhipuAI
import inspect
client = ZhipuAI(api_key="key")
print(inspect.getsource(client.file_parser.content))
