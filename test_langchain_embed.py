import asyncio
from langchain_google_genai import GoogleGenerativeAIEmbeddings
from src.shared.config import settings

async def debug():
    try:
        print("Instanciando com task_type...")
        e = GoogleGenerativeAIEmbeddings(model="models/text-embedding-004", task_type="retrieval_document", google_api_key=settings.GOOGLE_API_KEY)
        res = await e.aembed_query("Ola")
        print("Success! Size:", len(res))
    except Exception as ex:
        import traceback
        traceback.print_exc()

asyncio.run(debug())
