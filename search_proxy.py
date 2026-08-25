import json
import re
import requests
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs, unquote

PORT = 8001
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36"


def ddg_search(query):
    try:
        r = requests.post("https://html.duckduckgo.com/html/", data={"q": query},
                          headers={"User-Agent": UA}, timeout=20)
        if not r.ok:
            return []
        text = r.text
        anchors = re.findall(r'class="result__a"[^>]*href="([^"]+)"[^>]*>(.*?)</a>', text, re.DOTALL)
        snips = re.findall(r'class="result__snippet"[^>]*>(.*?)</div>', text, re.DOTALL)

        def clean(s):
            s = re.sub(r"<[^>]+>", "", s or "")
            s = re.sub(r"&[a-z]+;", " ", s)
            return s.strip()

        results = []
        for i, (href, title_html) in enumerate(anchors[:8]):
            title = clean(title_html)
            sn = clean(snips[i]) if i < len(snips) else ""
            m = re.search(r"uddg=([^&]+)", href)
            url = unquote(m.group(1)) if m else (href if href.startswith("http") else "")
            if title and url:
                results.append({"title": title, "url": url, "snippet": sn})
        return results
    except Exception:
        return []


class Handler(BaseHTTPRequestHandler):
    def _cors(self):
        self.send_header("Access-Control-Allow-Origin", "*")

    def do_GET(self):
        p = urlparse(self.path)
        if p.path == "/search":
            q = parse_qs(p.query).get("q", [""])[0]
            body = json.dumps(ddg_search(q), ensure_ascii=False).encode("utf-8")
            self.send_response(200)
            self._cors()
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"ok")

    def log_message(self, *a):
        pass


if __name__ == "__main__":
    print(f"search_proxy on :{PORT}")
    ThreadingHTTPServer(("", PORT), Handler).serve_forever()
