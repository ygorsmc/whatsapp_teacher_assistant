import os
import requests
from dotenv import load_dotenv

load_dotenv(".env.prod")
api_key = os.getenv("GLM_API")
base_url = "https://open.bigmodel.cn/api/paas/v4"
pdf_path = "_docs/docs/PMP_Exam_Prep.pdf"

headers = {"Authorization": f"Bearer {api_key}"}

with open(pdf_path, "rb") as f:
    files = {"file": (os.path.basename(pdf_path), f, "application/pdf")}
    data = {"tool_type": "zhipu-pro", "file_type": "pdf"}
    
    response = requests.post(f"{base_url}/files/parser", headers=headers, files=files, data=data)
    print("CN Endpoint Response:", response.json())
