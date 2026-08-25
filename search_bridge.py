import re
import time
import requests
import subprocess
import threading

CLOUDFLARED = "cloudflared.exe"
PROXY_PORT = 8001
BOT_URL = "https://assistant-bot-71lb.onrender.com"
TOKEN = "sbt_7f3a9c2e1b8d4a6f5c0e9b2d7a3f1c8e"
HEARTBEAT = 120
CURRENT_URL = {"v": None}


def extract_url(proc):
    for raw in iter(proc.stdout.readline, b""):
        line = raw.decode("utf-8", "replace")
        m = re.search(r"https://[a-z0-9\-]+\.trycloudflare\.com", line)
        if m:
            return m.group(0)
    return None


def push(url):
    try:
        r = requests.post(f"{BOT_URL}/_set_search_url", params={"token": TOKEN, "url": url}, timeout=15)
        print(f"[bridge] pushed {url} -> {r.status_code}")
    except Exception as e:
        print(f"[bridge] push failed: {e}")


def heartbeat():
    while True:
        time.sleep(HEARTBEAT)
        if CURRENT_URL["v"]:
            push(CURRENT_URL["v"])


def main():
    threading.Thread(target=heartbeat, daemon=True).start()
    while True:
        proc = subprocess.Popen(
            [CLOUDFLARED, "tunnel", "--url", f"http://localhost:{PROXY_PORT}"],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        )
        url = extract_url(proc)
        if url:
            CURRENT_URL["v"] = url
            print(f"[bridge] tunnel up: {url}")
            push(url)
            proc.wait()
            print("[bridge] tunnel exited, restarting...")
        else:
            print("[bridge] no tunnel URL, retry in 10s")
            try:
                proc.terminate()
            except Exception:
                pass
            time.sleep(10)


if __name__ == "__main__":
    main()
