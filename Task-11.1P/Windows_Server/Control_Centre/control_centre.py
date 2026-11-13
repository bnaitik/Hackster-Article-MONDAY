from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from pathlib import Path
import json
import os
import shutil
import socket
import subprocess
import threading
import time
import urllib.request
from urllib.error import URLError

ROOT = Path(__file__).resolve().parent
PROJECT = Path(r"C:\MondayServer")
STATIC = ROOT / "static"
LOG_DIR = PROJECT / "control_logs"
LOG_DIR.mkdir(exist_ok=True)

PY_AI = PROJECT / ".venv" / "Scripts" / "python.exe"
PY_STT = PROJECT / ".stt-venv" / "Scripts" / "python.exe"

SERVICES = {
    "ollama": {
        "label": "Ollama · Qwen",
        "port": 11434,
        "command": None,
        "health": "http://127.0.0.1:11434/api/tags",
        "log": "ollama.log",
    },
    "ai": {
        "label": "Monday AI",
        "port": 8000,
        "command": [str(PY_AI), "-m", "uvicorn", "server:app", "--host", "0.0.0.0", "--port", "8000"],
        "health": "http://127.0.0.1:8000/health",
        "log": "monday-ai.log",
    },
    "whisper": {
        "label": "Whisper STT",
        "port": 8001,
        "command": [str(PY_STT), "-m", "uvicorn", "transcription_server:app", "--host", "0.0.0.0", "--port", "8001"],
        "health": None,
        "log": "whisper.log",
    },
    "website": {
        "label": "Monday Website API",
        "port": 8002,
        "command": [str(PY_AI), "-m", "uvicorn", "monday_web:app", "--host", "127.0.0.1", "--port", "8002"],
        "health": "http://127.0.0.1:8002/",
        "log": "website.log",
    },
}

processes = {}
last_errors = {}
lock = threading.RLock()

def port_open(port):
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.35):
            return True
    except OSError:
        return False

def http_ok(url):
    if not url:
        return None
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Monday-Control-Centre"})
        with urllib.request.urlopen(req, timeout=1.2) as response:
            return 200 <= response.status < 500
    except Exception:
        return False

def status_one(key):
    cfg = SERVICES[key]
    with lock:
        proc = processes.get(key)
        error = last_errors.get(key, "")
        if proc is not None and proc.poll() is not None:
            processes.pop(key, None)
            if proc.returncode not in (0, None):
                error = error or f"Process exited with code {proc.returncode}."
                last_errors[key] = error
            proc = None

    listening = port_open(cfg["port"])
    health = http_ok(cfg["health"]) if listening and cfg["health"] else (None if not listening else True)

    if listening:
        managed = proc is not None
        state = "running" if (health is not False) else "starting"
        if key == "ollama" and not managed:
            detail = "Already running outside this controller; it will not be stopped by this dashboard."
        else:
            detail = "Managed by this dashboard." if managed else "Running externally."
    elif proc is not None:
        state = "starting"
        managed = True
        detail = "Process launched; waiting for its port."
    else:
        state = "error" if error else "stopped"
        managed = False
        detail = error or "Not running."

    return {
        "id": key, "label": cfg["label"], "port": cfg["port"],
        "state": state, "managed": managed, "detail": detail,
        "error": error
    }

def start_one(key):
    if key not in SERVICES:
        return {"success": False, "message": "Unknown service."}
    with lock:
        current = status_one(key)
        if current["state"] in ("running", "starting"):
            return {"success": True, "message": f'{SERVICES[key]["label"]} is already {current["state"]}.', "status": current}

        last_errors.pop(key, None)
        cfg = SERVICES[key]
        if port_open(cfg["port"]):
            return {"success": True, "message": f'{cfg["label"]} is already listening on port {cfg["port"]}.', "status": status_one(key)}

        if key == "ollama":
            exe = shutil.which("ollama")
            if not exe:
                candidates = [
                    Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Ollama" / "ollama.exe",
                    Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "Ollama" / "ollama.exe",
                ]
                exe = next((str(p) for p in candidates if p.exists()), None)
            if not exe:
                msg = "Could not find ollama.exe. Install location was not detected."
                last_errors[key] = msg
                return {"success": False, "message": msg, "status": status_one(key)}
            command = [exe, "serve"]
        else:
            command = cfg["command"]
            if not Path(command[0]).exists():
                msg = f'Python environment not found: {command[0]}'
                last_errors[key] = msg
                return {"success": False, "message": msg, "status": status_one(key)}

        try:
            log_path = LOG_DIR / cfg["log"]
            log_handle = open(log_path, "a", encoding="utf-8", buffering=1)
            creationflags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
            proc = subprocess.Popen(
                command, cwd=str(PROJECT), stdin=subprocess.DEVNULL,
                stdout=log_handle, stderr=subprocess.STDOUT,
                creationflags=creationflags, close_fds=True
            )
            processes[key] = proc
            process_log_handles[key] = log_handle
            time.sleep(0.25)
            if proc.poll() is not None:
                msg = f'Process exited immediately with code {proc.returncode}. Check {log_path}.'
                last_errors[key] = msg
                processes.pop(key, None)
                close_log(key)
                return {"success": False, "message": msg, "status": status_one(key)}
            return {"success": True, "message": f'Started {cfg["label"]}.', "status": status_one(key)}
        except Exception as exc:
            msg = f"Could not start {cfg['label']}: {exc}"
            last_errors[key] = msg
            return {"success": False, "message": msg, "status": status_one(key)}

process_log_handles = {}

def close_log(key):
    handle = process_log_handles.pop(key, None)
    if handle:
        try:
            handle.close()
        except Exception:
            pass

def stop_one(key):
    if key not in SERVICES:
        return {"success": False, "message": "Unknown service."}
    with lock:
        proc = processes.get(key)
        if proc is None:
            if port_open(SERVICES[key]["port"]):
                return {"success": False, "message": f'{SERVICES[key]["label"]} is running outside this controller. For safety, stop it from the window/service that started it.'}
            return {"success": True, "message": f'{SERVICES[key]["label"]} is already stopped.'}
        try:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=3)
            processes.pop(key, None)
            close_log(key)
            last_errors.pop(key, None)
            return {"success": True, "message": f'Stopped {SERVICES[key]["label"]}.'}
        except Exception as exc:
            msg = f"Could not stop {SERVICES[key]['label']}: {exc}"
            last_errors[key] = msg
            return {"success": False, "message": msg}

class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        print("%s - %s" % (self.address_string(), fmt % args))

    def send_json(self, data, code=200):
        body = json.dumps(data).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/api/status":
            self.send_json({"services": [status_one(k) for k in SERVICES]})
            return
        if self.path in ("/", "/index.html"):
            path = STATIC / "index.html"
        elif self.path == "/style.css":
            path = STATIC / "style.css"
        elif self.path == "/app.js":
            path = STATIC / "app.js"
        else:
            self.send_error(404)
            return
        if not path.exists():
            self.send_error(404, "Dashboard asset not found")
            return
        content = path.read_bytes()
        mime = "text/html; charset=utf-8" if path.suffix == ".html" else ("text/css; charset=utf-8" if path.suffix == ".css" else "application/javascript; charset=utf-8")
        self.send_response(200)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        self.wfile.write(content)

    def do_POST(self):
        parts = self.path.strip("/").split("/")
        if len(parts) == 3 and parts[0] == "api" and parts[1] in ("start", "stop"):
            key = parts[2]
            result = start_one(key) if parts[1] == "start" else stop_one(key)
            self.send_json(result, 200 if result["success"] else 400)
            return
        if self.path == "/api/start-all":
            results = []
            results.append(start_one("ollama"))
            deadline = time.time() + 18
            while time.time() < deadline and not port_open(11434):
                time.sleep(0.4)
            results.append(start_one("ai"))
            results.append(start_one("whisper"))
            results.append(start_one("website"))
            self.send_json({"success": all(x["success"] for x in results), "results": results})
            return
        if self.path == "/api/stop-all":
            results = [stop_one(k) for k in ("website", "whisper", "ai", "ollama")]
            self.send_json({"success": all(x["success"] for x in results), "results": results})
            return
        self.send_error(404)

if __name__ == "__main__":
    if not PROJECT.exists():
        raise SystemExit("C:\\MondayServer was not found.")
    server = ThreadingHTTPServer(("127.0.0.1", 8003), Handler)
    print("Monday Control Centre: http://127.0.0.1:8003")
    print("Close this window to stop the control panel. Use its Stop buttons to stop managed services.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
