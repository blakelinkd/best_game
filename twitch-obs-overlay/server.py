#!/usr/bin/env python3
import http.server
import json
import pathlib
import ssl
import socketserver
import urllib.parse
import urllib.error
import urllib.request


HOST = "127.0.0.1"
PORT = 3757
ROOT = pathlib.Path(__file__).resolve().parent
CONFIG_PATH = ROOT / "config.json"
CERT_PATH = ROOT / "localhost.pem"
KEY_PATH = ROOT / "localhost-key.pem"
STATE = {
    "access_token": "",
    "expires_at": 0,
    "login": "",
    "user_id": "",
}


def read_config():
    if not CONFIG_PATH.exists():
        return {
            "channel": "biotachyonic",
            "client_id": "",
        }
    try:
        with CONFIG_PATH.open("r", encoding="utf-8") as handle:
            config = json.load(handle)
    except (OSError, json.JSONDecodeError):
        config = {}
    return {
        "channel": str(config.get("channel") or "biotachyonic").strip().lower(),
        "client_id": str(config.get("client_id") or "").strip(),
    }


def write_config(config):
    clean = {
        "channel": str(config.get("channel") or "biotachyonic").strip().lower(),
        "client_id": str(config.get("client_id") or "").strip(),
    }
    with CONFIG_PATH.open("w", encoding="utf-8") as handle:
        json.dump(clean, handle, indent=2)
        handle.write("\n")
    return clean


class Handler(http.server.SimpleHTTPRequestHandler):
    server_version = "TwitchOBSOverlay/1.0"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(ROOT), **kwargs)

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path == "/api/config":
            self.send_json({**read_config(), "redirect_uri": self.redirect_uri()})
            return
        if parsed.path == "/api/session":
            self.send_json({
                "authenticated": bool(STATE["access_token"]),
                "login": STATE["login"],
                "user_id": STATE["user_id"],
                "expires_at": STATE["expires_at"],
            })
            return
        if parsed.path == "/api/helix":
            self.proxy_helix(parsed)
            return
        if parsed.path == "/overlay":
            self.path = "/overlay.html"
        elif parsed.path == "/setup":
            self.path = "/setup.html"
        super().do_GET()

    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)
        length = int(self.headers.get("Content-Length") or "0")
        raw_body = self.rfile.read(length).decode("utf-8")
        try:
            body = json.loads(raw_body or "{}")
        except json.JSONDecodeError:
            self.send_json({"error": "Invalid JSON"}, status=400)
            return

        if parsed.path == "/api/config":
            self.send_json(write_config(body))
            return

        if parsed.path == "/api/session":
            STATE["access_token"] = str(body.get("access_token") or "")
            STATE["expires_at"] = int(body.get("expires_at") or 0)
            STATE["login"] = str(body.get("login") or "")
            STATE["user_id"] = str(body.get("user_id") or "")
            self.send_json({
                "authenticated": bool(STATE["access_token"]),
                "login": STATE["login"],
                "user_id": STATE["user_id"],
                "expires_at": STATE["expires_at"],
            })
            return

        if parsed.path == "/api/logout":
            STATE.update({
                "access_token": "",
                "expires_at": 0,
                "login": "",
                "user_id": "",
            })
            self.send_json({"authenticated": False})
            return

        self.send_json({"error": "Not found"}, status=404)

    def end_headers(self):
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def send_json(self, payload, status=200):
        encoded = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def proxy_helix(self, parsed):
        config = read_config()
        query = urllib.parse.parse_qs(parsed.query)
        path = (query.get("path") or [""])[0]
        if not STATE["access_token"]:
            self.send_json({"error": "Not authenticated"}, status=401)
            return
        if not config["client_id"]:
            self.send_json({"error": "Missing client_id"}, status=400)
            return
        if not path.startswith("/") or path.startswith("//"):
            self.send_json({"error": "Invalid Helix path"}, status=400)
            return

        request = urllib.request.Request(
            "https://api.twitch.tv/helix" + path,
            headers={
                "Authorization": "Bearer " + STATE["access_token"],
                "Client-Id": config["client_id"],
            },
        )

        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                payload = response.read()
                status = response.status
                content_type = response.headers.get("Content-Type", "application/json")
        except urllib.error.HTTPError as error:
            payload = error.read()
            status = error.code
            content_type = error.headers.get("Content-Type", "application/json")
        except urllib.error.URLError as error:
            self.send_json({"error": str(error)}, status=502)
            return

        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def redirect_uri(self):
        return f"https://{HOST}:{PORT}/setup.html"


def main():
    if not CERT_PATH.exists() or not KEY_PATH.exists():
        raise SystemExit(
            "Missing HTTPS certificate files. Run ./make-cert.sh first."
        )

    socketserver.TCPServer.allow_reuse_address = True
    with socketserver.TCPServer((HOST, PORT), Handler) as httpd:
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(certfile=CERT_PATH, keyfile=KEY_PATH)
        httpd.socket = context.wrap_socket(httpd.socket, server_side=True)
        print(f"Setup:   https://{HOST}:{PORT}/setup")
        print(f"Overlay: https://{HOST}:{PORT}/overlay")
        print("Press Ctrl+C to stop.")
        httpd.serve_forever()


if __name__ == "__main__":
    main()
