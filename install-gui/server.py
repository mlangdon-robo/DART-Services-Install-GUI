#!/usr/bin/env python3
"""Local installer for Dart Services Compose.

Follows the Quick Start in the project README:
check a container runtime, write .env, then start Compose.

    python install-gui/server.py
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import threading
import webbrowser
import winreg
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ENV_PATH = ROOT / ".env"
EXAMPLE_PATH = ROOT / ".env.example"
HTML_PATH = Path(__file__).resolve().parent / "index.html"
PROJECT = "dart-services-compose"
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

KEYS = ("SDK_VERSION", "ROBOT_MODEL", "SIMULATOR_CPU", "SIMULATOR_MEMORY")
MODELS = {
    "M0609",
    "M0617",
    "M1013",
    "M1509",
    "A0509",
    "A0912",
    "H2017",
    "H2515",
    "E0509",
    "P3020",
}
KEY_LINE = re.compile(r"^(\s*)([A-Za-z_][A-Za-z0-9_]*)(\s*=\s*)(.*)$")
ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
VALIDATORS = {
    "SDK_VERSION": re.compile(r"^sdk(?:[2-9]|[1-9]\d)$"),
    "ROBOT_MODEL": re.compile(r"^[A-Za-z][A-Za-z0-9]{3,15}$"),
    "SIMULATOR_CPU": re.compile(r"^(?:0\.[1-9]\d?|[1-9]\d{0,2}(?:\.\d{1,2})?)$"),
    "SIMULATOR_MEMORY": re.compile(r"^\d+(?:\.\d+)?[kKmMgG](?:[iI]?[bB])?$"),
}
PORTS = (
    (12345, "TCP", "simulator"),
    (3601, "TCP", "simulator"),
    (502, "TCP", "simulator"),
    (12360, "UDP", "simulator"),
    (5022, "TCP", "build-module-fw"),
    (3002, "TCP", "build-module-ui"),
    (8089, "UDP", "build-module-ui"),
    (5023, "TCP", "build-module-ui"),
)


class Job:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.running = False
        self.action = ""
        self.log = ""
        self.exit_code: int | None = None

    def snapshot(self) -> dict:
        with self.lock:
            return {
                "running": self.running,
                "action": self.action,
                "log": self.log,
                "exitCode": self.exit_code,
            }

    def begin(self, action: str) -> bool:
        with self.lock:
            if self.running:
                return False
            self.running = True
            self.action = action
            self.log = ""
            self.exit_code = None
            return True

    def append(self, text: str) -> None:
        cleaned = ANSI.sub("", text).replace("\r\n", "\n").replace("\r", "\n")
        with self.lock:
            self.log = (self.log + cleaned)[-200_000:]

    def finish(self, code: int) -> None:
        with self.lock:
            self.running = False
            self.exit_code = code


JOB = Job()


def parse_env(text: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in text.splitlines():
        match = KEY_LINE.match(line)
        if not match:
            continue
        values[match.group(2)] = match.group(4).strip()
    return values


def update_env(text: str, updates: dict[str, str]) -> str:
    newline = "\r\n" if "\r\n" in text else "\n"
    ends_with_newline = text.endswith("\n")
    seen: set[str] = set()
    updated: list[str] = []
    for line in text.splitlines():
        match = KEY_LINE.match(line)
        if match and match.group(2) in updates:
            key = match.group(2)
            seen.add(key)
            updated.append(f"{match.group(1)}{key}{match.group(3)}{updates[key]}")
        else:
            updated.append(line)
    missing = [key for key in KEYS if key in updates and key not in seen]
    if missing and updated and updated[-1].strip():
        updated.append("")
    for key in missing:
        updated.append(f"{key}={updates[key]}")
    result = newline.join(updated)
    if ends_with_newline:
        result += newline
    return result


def validate(updates: dict[str, str], existing: dict[str, str]) -> str | None:
    if set(updates) != set(KEYS):
        return "Send SDK_VERSION, ROBOT_MODEL, SIMULATOR_CPU, and SIMULATOR_MEMORY."
    for key, value in updates.items():
        if not isinstance(value, str) or not value.strip():
            return f"{key} cannot be empty."
        cleaned = value.strip()
        updates[key] = cleaned
        if key == "ROBOT_MODEL" and cleaned not in MODELS and cleaned != existing.get(key):
            return f"{cleaned} is not a known robot model."
        if not VALIDATORS[key].fullmatch(cleaned) and cleaned != existing.get(key):
            return f"{key} has an unsupported value."
        if key == "SIMULATOR_CPU" and float(cleaned) > 128:
            return "SIMULATOR_CPU must be 128 or less."
    return None


def apply_env(values: dict) -> str | None:
    source = ENV_PATH if ENV_PATH.is_file() else EXAMPLE_PATH
    if not source.is_file():
        return ".env.example was not found."
    current = source.read_text(encoding="utf-8", newline="")
    existing = parse_env(current)
    updates = {key: values.get(key, "") for key in KEYS}
    error = validate(updates, existing)
    if error:
        return error
    ENV_PATH.write_bytes(update_env(current, updates).encode("utf-8"))
    return None


def merged_path() -> str:
    parts: list[str] = []
    for hive, subkey in (
        (winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment"),
        (winreg.HKEY_CURRENT_USER, r"Environment"),
    ):
        try:
            with winreg.OpenKey(hive, subkey) as key:
                raw, _ = winreg.QueryValueEx(key, "Path")
        except OSError:
            continue
        parts.extend(os.path.expandvars(str(raw)).split(os.pathsep))
    parts.extend(os.environ.get("PATH", "").split(os.pathsep))
    seen: list[str] = []
    known = set()
    for part in parts:
        token = part.lower()
        if part and token not in known:
            known.add(token)
            seen.append(part)
    return os.pathsep.join(seen)


def command_env() -> dict[str, str]:
    env = os.environ.copy()
    env["PATH"] = merged_path()
    return env


def which(name: str) -> str | None:
    return shutil.which(name, path=command_env()["PATH"])


def run(args: list[str], timeout: float = 20) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        args,
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        env=command_env(),
        creationflags=NO_WINDOW,
    )


def first_line(text: str) -> str:
    for line in text.splitlines():
        if line.strip():
            return line.strip()
    return ""


def process_running(image: str) -> bool:
    try:
        result = run(["tasklist", "/FI", f"IMAGENAME eq {image}", "/NH"], timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        return False
    return image.lower() in result.stdout.lower()


def compose_base() -> list[str]:
    return ["docker", "compose", "-p", PROJECT, "--project-directory", str(ROOT)]


def read_values() -> dict[str, str]:
    source = ENV_PATH if ENV_PATH.is_file() else EXAMPLE_PATH
    if not source.is_file():
        return {key: "" for key in KEYS}
    parsed = parse_env(source.read_text(encoding="utf-8", newline=""))
    return {key: parsed.get(key, "") for key in KEYS}


def docker_status() -> dict:
    status = {
        "cli": False,
        "clientVersion": "",
        "composeOk": False,
        "composeVersion": "",
        "composeError": "",
        "daemonOk": False,
        "serverVersion": "",
        "daemonError": "",
        "dockerDesktop": process_running("Docker Desktop.exe"),
        "rancher": process_running("Rancher Desktop.exe"),
    }
    if not which("docker"):
        status["daemonError"] = "Docker was not found on PATH."
        return status
    status["cli"] = True
    try:
        client = run(["docker", "version", "--format", "{{.Client.Version}}"], timeout=8)
        status["clientVersion"] = first_line(client.stdout)
    except (OSError, subprocess.TimeoutExpired) as exc:
        status["daemonError"] = str(exc)
        return status
    try:
        compose = run(["docker", "compose", "version", "--short"], timeout=12)
    except subprocess.TimeoutExpired:
        status["composeError"] = "docker compose version timed out."
        return status
    if compose.returncode != 0:
        status["composeError"] = first_line(compose.stderr) or first_line(compose.stdout) or "docker compose failed."
        return status
    status["composeOk"] = True
    status["composeVersion"] = first_line(compose.stdout)
    try:
        info = run(["docker", "info", "--format", "{{.ServerVersion}}"], timeout=8)
    except subprocess.TimeoutExpired:
        status["daemonError"] = "Cannot connect to the Docker daemon. Start Docker Desktop or Rancher Desktop and wait about a minute."
        return status
    version = first_line(info.stdout)
    detail = (info.stderr or "") + "\n" + (info.stdout or "")
    if info.returncode != 0 or not version or "nil pointer" in detail.lower() or "cannot connect" in detail.lower():
        status["daemonError"] = "Cannot connect to the Docker daemon. Start Docker Desktop or Rancher Desktop and wait about a minute."
        return status
    status["daemonOk"] = True
    status["serverVersion"] = version
    return status


def container_status() -> list[dict]:
    if not which("docker"):
        return []
    try:
        result = run(compose_base() + ["ps", "-a", "--format", "json"], timeout=20)
    except (OSError, subprocess.TimeoutExpired):
        return []
    if result.returncode != 0:
        return []
    text = result.stdout.strip()
    if not text:
        return []
    raw_items: list = []
    try:
        if text.startswith("["):
            raw_items = json.loads(text)
        else:
            raw_items = [json.loads(line) for line in text.splitlines() if line.strip()]
    except json.JSONDecodeError:
        return []
    containers = []
    for item in raw_items:
        if not isinstance(item, dict):
            continue
        lowered = {str(key).lower(): value for key, value in item.items()}
        containers.append(
            {
                "name": str(lowered.get("name") or ""),
                "service": str(lowered.get("service") or ""),
                "state": str(lowered.get("state") or ""),
                "status": str(lowered.get("status") or ""),
            }
        )
    return containers


def process_names(pids: set[str]) -> dict[str, str]:
    names: dict[str, str] = {}
    for pid in pids:
        if not pid.isdigit() or pid in {"0", "4"}:
            names[pid] = "System"
            continue
        try:
            result = run(["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"], timeout=8)
        except (OSError, subprocess.TimeoutExpired):
            continue
        line = first_line(result.stdout)
        if line.startswith('"'):
            names[pid] = line.split('"', 2)[1]
    return names


def port_conflicts(containers_running: bool) -> list[dict]:
    if containers_running:
        return []
    try:
        result = run(["netstat", "-ano"], timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        return []
    wanted = {(proto, str(port)): service for port, proto, service in PORTS}
    found: dict[tuple[str, str], str] = {}
    for line in result.stdout.splitlines():
        parts = line.split()
        if len(parts) < 4 or parts[0].upper() not in {"TCP", "UDP"}:
            continue
        proto = parts[0].upper()
        port = parts[1].rsplit(":", 1)[-1]
        if (proto, port) not in wanted:
            continue
        if proto == "TCP" and (len(parts) < 5 or parts[3].upper() != "LISTENING"):
            continue
        pid = parts[-1]
        found[(proto, port)] = pid
    names = process_names(set(found.values()))
    conflicts = []
    for (proto, port), pid in sorted(found.items(), key=lambda item: int(item[0][1])):
        conflicts.append(
            {
                "port": int(port),
                "proto": proto,
                "service": wanted[(proto, port)],
                "pid": pid,
                "process": names.get(pid, ""),
            }
        )
    return conflicts


def status_payload() -> dict:
    docker = docker_status()
    containers = container_status() if docker["daemonOk"] else []
    running = any(item["state"].lower() == "running" for item in containers)
    git = which("git")
    git_version = ""
    if git:
        try:
            result = run(["git", "--version"], timeout=8)
            git_version = first_line(result.stdout)
        except (OSError, subprocess.TimeoutExpired):
            git_version = ""
    return {
        "repo": str(ROOT),
        "envExists": ENV_PATH.is_file(),
        "exampleExists": EXAMPLE_PATH.is_file(),
        "values": read_values(),
        "git": git_version,
        "winget": bool(which("winget")),
        "docker": docker,
        "containers": containers,
        "running": running,
        "ports": port_conflicts(running),
    }


def command_for(action: str, profile: str) -> list[list[str]]:
    build = profile != "simulator"
    if action == "install-docker":
        return [["winget", "install", "--id", "Docker.DockerDesktop", "-e", "--accept-package-agreements", "--accept-source-agreements"]]
    if action == "install-rancher":
        return [["winget", "install", "--id", "suse.RancherDesktop", "-e", "--accept-package-agreements", "--accept-source-agreements"]]
    if action == "up":
        command = compose_base()
        if build:
            command += ["--profile", "build"]
        return [command + ["up", "-d"]]
    if action == "down":
        return [compose_base() + ["--profile", "build", "down"]]
    if action == "update":
        pull = compose_base()
        again = compose_base()
        if build:
            pull += ["--profile", "build"]
            again += ["--profile", "build"]
        return [pull + ["pull"], again + ["up", "-d"]]
    if action == "remove-orphans":
        return orphan_commands()
    return []


def orphan_commands() -> list[list[str]]:
    try:
        result = run(["docker", "ps", "-a", "--format", "{{.Names}}"], timeout=15)
    except (OSError, subprocess.TimeoutExpired):
        return [["docker", "rm", "-f", "simulator", "build-module-fw", "build-module-ui"]]
    present = result.stdout.split()
    names = [name for name in ("simulator", "build-module-fw", "build-module-ui") if name in present]
    if not names:
        return []
    return [["docker", "rm", "-f", *names]]


def run_job(commands: list[list[str]]) -> None:
    code = 0
    try:
        if not commands:
            JOB.append("No leftover containers were found.\n")
        for command in commands:
            if command[0] == "winget":
                JOB.append("Windows may ask you to approve the installer. This can take several minutes.\n")
            JOB.append("$ " + " ".join(command) + "\n")
            try:
                proc = subprocess.Popen(
                    command,
                    cwd=ROOT,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    env=command_env(),
                    creationflags=NO_WINDOW,
                )
            except OSError as exc:
                JOB.append(str(exc) + "\n")
                code = 1
                break
            assert proc.stdout is not None
            while True:
                chunk = proc.stdout.read(1024)
                if not chunk:
                    break
                JOB.append(chunk)
            finished = proc.wait()
            if finished != 0:
                code = finished
                break
    except Exception as exc:  # noqa: BLE001 — surface any job failure in the log
        JOB.append(str(exc) + "\n")
        code = 1
    JOB.finish(code)


class Handler(BaseHTTPRequestHandler):
    server_version = "DartInstaller/1.0"

    def log_message(self, fmt: str, *args) -> None:
        print(f"[{self.log_date_time_string()}] {fmt % args}", flush=True)

    def _send(self, status: int, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _send_json(self, status: int, payload: dict) -> None:
        self._send(status, json.dumps(payload).encode("utf-8"), "application/json; charset=utf-8")

    def _read_json(self) -> dict | None:
        length = int(self.headers.get("Content-Length", "0") or "0")
        if length <= 0 or length > 32_768:
            self._send_json(400, {"error": "Request body is empty or too large."})
            return None
        try:
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            self._send_json(400, {"error": "Body must be JSON."})
            return None
        if not isinstance(payload, dict):
            self._send_json(400, {"error": "Expected a JSON object."})
            return None
        return payload

    def do_GET(self) -> None:  # noqa: N802
        path = self.path.split("?", 1)[0]
        if path in ("/", "/index.html"):
            if not HTML_PATH.is_file():
                self._send_json(500, {"error": "Installer page is missing."})
                return
            self._send(200, HTML_PATH.read_bytes(), "text/html; charset=utf-8")
            return
        if path == "/api/status":
            self._send_json(200, status_payload())
            return
        if path == "/api/job":
            self._send_json(200, JOB.snapshot())
            return
        self._send_json(404, {"error": "Not found."})

    def do_POST(self) -> None:  # noqa: N802
        path = self.path.split("?", 1)[0]
        payload = self._read_json()
        if payload is None:
            return
        if path == "/api/env":
            values = payload.get("values")
            if not isinstance(values, dict):
                self._send_json(400, {"error": "Expected a values object."})
                return
            error = apply_env(values)
            if error:
                self._send_json(400, {"error": error})
                return
            self._send_json(200, {"values": read_values(), "saved": True})
            return
        if path != "/api/job":
            self._send_json(404, {"error": "Not found."})
            return
        action = payload.get("action")
        profile = payload.get("profile") if payload.get("profile") in {"build", "simulator"} else "build"
        if action not in {"install-docker", "install-rancher", "up", "down", "update", "remove-orphans"}:
            self._send_json(400, {"error": "Unknown action."})
            return
        if action in {"up", "update"}:
            values = payload.get("values")
            if not isinstance(values, dict):
                self._send_json(400, {"error": "Save the simulator settings before starting."})
                return
            error = apply_env(values)
            if error:
                self._send_json(400, {"error": error})
                return
        commands = command_for(str(action), str(profile))
        if not JOB.begin(str(action)):
            self._send_json(409, {"error": "Another install task is still running."})
            return
        threading.Thread(target=run_job, args=(commands,), daemon=True).start()
        self._send_json(200, JOB.snapshot())


def main() -> None:
    parser = argparse.ArgumentParser(description="Install Dart Services Compose from a browser.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8766)
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()
    httpd = ThreadingHTTPServer((args.host, args.port), Handler)
    url = f"http://{args.host}:{args.port}"
    print(f"Dart Services installer: {url}", flush=True)
    print(f"Project: {ROOT}", flush=True)
    if not args.no_browser:
        webbrowser.open(url)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        httpd.server_close()


if __name__ == "__main__":
    main()
