import time
import urllib.request
import json
import re
import os
import subprocess

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
        else:
            print("[WARNING] .env file not found. Could not update NGROK_URL.")
            
    except Exception as e:
        print(f"[ERROR] Failed to fetch Ngrok URL: {e}")
        print("Make sure ngrok is authenticated and installed correctly.")

if __name__ == "__main__":
    main()
