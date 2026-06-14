import os
import time
import requests
from dotenv import load_dotenv

def test_glm_parser_http():
    # Carrega a API key do .env.prod
    load_dotenv(".env.prod")
    api_key = os.getenv("GLM_API")
    
    if not api_key:
        print("Erro: Chave 'GLM_API' não encontrada no .env.prod")
        return

    # Forçando explicitamente a rota internacional da Z.ai via HTTP
    base_url = "https://api.z.ai/api/paas/v4"
    pdf_path = "_docs/docs/PMP_Exam_Prep.pdf"
    output_path = "_docs/docs/PMP_Exam_Prep.md"

    if not os.path.exists(pdf_path):
        print(f"Erro: O arquivo {pdf_path} não existe.")
        return

    headers = {
        "Authorization": f"Bearer {api_key}"
    }

    print(f"Enviando via POST HTTP Puro para {base_url}/files/parser/create...")
    
    try:
        with open(pdf_path, "rb") as f:
            files = {
                "file": (os.path.basename(pdf_path), f, "application/pdf")
            }
            data = {
                "tool_type": "zhipu-pro",
                "file_type": "pdf"
            }
            
            response = requests.post(
                f"{base_url}/files/parser/create",
                headers=headers,
                files=files,
                data=data
            )
            
        if response.status_code != 200:
            print(f"Falha na requisição (HTTP {response.status_code}): {response.text}")
            return

        res_json = response.json()
        
        # Verifica erro explícito no corpo
        if res_json.get("code") not in [None, 200, 0]:
            print("Erro na API:", res_json)
            return

        # Pega o task_id
        task_id = res_json.get("task_id") or res_json.get("data", {}).get("task_id")
        
        if not task_id:
            print("A API não retornou um task_id. Resposta bruta:", res_json)
            return

        print(f"Tarefa criada com sucesso! Task ID: {task_id}")
        print("Aguardando o processamento do documento (isso pode demorar)...")

        # Polling do resultado
        max_attempts = 30
        for attempt in range(max_attempts):
            poll_response = requests.get(
                f"{base_url}/files/parser/result/{task_id}/text",
                headers=headers
            )
            
            content_type = poll_response.headers.get("Content-Type", "")
            
            if "application/json" in content_type:
                # Se for JSON, Zhipu está retornando status de progresso ou erro
                try:
                    status_json = poll_response.json()
                    code = status_json.get("code")
                    msg = status_json.get("msg", "")
                    
                    if "processing" in msg.lower() or code == 500:
                        print(f"[{attempt + 1}/{max_attempts}] Processando...")
                        time.sleep(10)
                    else:
                        print(f"Erro ao obter o resultado: {status_json}")
                        return
                except ValueError:
                    print(f"[{attempt + 1}/{max_attempts}] Resposta JSON inválida, tentando novamente...")
                    time.sleep(10)
            else:
                # Se não for JSON e retornar 200, é o nosso markdown!
                if poll_response.status_code == 200:
                    with open(output_path, "wb") as out:
                        out.write(poll_response.content)
                    print(f"\n✅ Extração concluída com sucesso! Resultado salvo em: {output_path}")
                    return
                else:
                    print(f"[{attempt + 1}/{max_attempts}] Aguardando (HTTP {poll_response.status_code})...")
                    time.sleep(10)

        print("\n❌ Timeout: O processamento não foi concluído no tempo estipulado.")

    except Exception as e:
        print(f"Exceção durante a requisição HTTP: {e}")

if __name__ == "__main__":
    test_glm_parser_http()
