import time
import urllib.request
import urllib.parse
import json
import re
import os
import subprocess


def _read_env_value(content: str, key: str) -> str:
    """Lê KEY=valor do conteúdo do .env (com ou sem aspas)."""
    m = re.search(rf'^{re.escape(key)}=(.*)$', content, re.M)
    return m.group(1).strip().strip('"').strip("'") if m else ""


def register_telegram_webhook(base_url: str, content: str) -> None:
    """Re-aponta o webhook do Telegram para a URL ATUAL do ngrok (idempotente).

    O ngrok-free troca a URL a cada start; sem re-registrar, o Telegram fica preso
    na última URL (ex.: o prod morto) e o local nunca recebe. O secret_token é
    OBRIGATÓRIO — o app rejeita POSTs sem ele (webhook_signature_failed)."""
    token = _read_env_value(content, "TELEGRAM_BOT_TOKEN")
    secret = _read_env_value(content, "TELEGRAM_WEBHOOK_SECRET")
    if not token or token in ("dev_token", "YOUR_TELEGRAM_TOKEN"):
        print("[INFO] Telegram: TELEGRAM_BOT_TOKEN ausente/placeholder — pulando setWebhook.")
        return
    if not secret or secret == "YOUR_TELEGRAM_SECRET":
        print("[WARNING] Telegram: TELEGRAM_WEBHOOK_SECRET ausente — app rejeitaria os POSTs. Pulando setWebhook.")
        return

    webhook_url = f"{base_url}/v1/webhook/telegram"
    payload = urllib.parse.urlencode({"url": webhook_url, "secret_token": secret}).encode()
    try:
        req = urllib.request.Request(f"https://api.telegram.org/bot{token}/setWebhook", data=payload)
        resp = json.loads(urllib.request.urlopen(req, timeout=10).read().decode("utf-8"))
        if resp.get("ok"):
            print(f"[INFO] Telegram webhook registrado → {webhook_url}")
        else:
            print(f"[WARNING] Telegram setWebhook falhou: {resp.get('description', resp)}")
    except Exception as e:
        print(f"[WARNING] Telegram setWebhook erro: {e}")


def is_ngrok_running():
    try:
        urllib.request.urlopen("http://127.0.0.1:4040/api/tunnels", timeout=2)
        return True
    except:
        return False

def main():
    if not is_ngrok_running():
        print("[INFO] Starting Ngrok in background...")
        # Start ngrok quietly in the background, in a separate session so it survives Ctrl+C
        subprocess.Popen(
            ["ngrok", "http", "8000"], 
            stdout=subprocess.DEVNULL, 
            stderr=subprocess.DEVNULL,
            start_new_session=True
        )
        # Wait for the tunnel to initialize
        time.sleep(3)
    else:
        print("[INFO] Ngrok is already running. Reusing existing tunnel.")
    
    try:
        req = urllib.request.urlopen("http://127.0.0.1:4040/api/tunnels")
        data = json.loads(req.read().decode('utf-8'))
        
        # Get the HTTPS tunnel URL
        url = None
        for tunnel in data.get('tunnels', []):
            if tunnel['public_url'].startswith('https'):
                url = tunnel['public_url']
                break
                
        if not url:
            raise Exception("Could not find HTTPS tunnel URL")
            
        print(f"\n✅ Ngrok Tunnel is Active: \033[92m{url}\033[0m")
        print(f"Webhook URL for Meta: \033[93m{url}/v1/webhook\033[0m\n")
        
        # Update the .env file
        env_path = ".env"
        if os.path.exists(env_path):
            with open(env_path, "r") as f:
                content = f.read()
            
            # Replace existing NGROK_URL or append it
            if "NGROK_URL=" in content:
                content = re.sub(r'NGROK_URL=.*', f'NGROK_URL="{url}"', content)
            else:
                content += f'\nNGROK_URL="{url}"\n'
                
            with open(env_path, "w") as f:
                f.write(content)

            print("[INFO] .env file successfully updated with new NGROK_URL.")

            # Re-aponta o webhook do Telegram para esta URL nova (o app valida o
            # secret_token; o Meta/WhatsApp continua sendo registro manual no painel).
            register_telegram_webhook(url, content)
        else:
            print("[WARNING] .env file not found. Could not update NGROK_URL.")
            
    except Exception as e:
        print(f"[ERROR] Failed to fetch Ngrok URL: {e}")
        print("Make sure ngrok is authenticated and installed correctly.")

if __name__ == "__main__":
    main()
