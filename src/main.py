"""WASH module: v2RayTun VLESS client (Xray-core SOCKS/HTTP proxy)."""

from __future__ import annotations

import base64
import json
import os
import platform
import signal
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

DATA_DIR = Path(os.environ.get("MODULE_DATA_DIR", "/data"))
SHARE_LINK = os.environ.get("SHARE_LINK_SECRET", "").strip()
SOCKS_PORT = int(os.environ.get("SOCKS_PORT", "10808"))
HTTP_PORT = int(os.environ.get("HTTP_PORT", "10809"))
SERVER_INDEX = int(os.environ.get("SERVER_INDEX", "0"))
REFRESH_INTERVAL = int(os.environ.get("REFRESH_INTERVAL", "3600"))
HEALTHCHECK_INTERVAL = int(os.environ.get("HEALTHCHECK_INTERVAL", "60"))
HEALTHCHECK_URL = os.environ.get("HEALTHCHECK_URL", "https://cp.cloudflare.com/").strip()
XRAY_VERSION = os.environ.get("XRAY_VERSION", "v26.3.27").strip() or "v26.3.27"

BIN_DIR = DATA_DIR / "bin"
XRAY_BIN = BIN_DIR / "xray"
CONFIG_PATH = DATA_DIR / "xray-config.json"
SNAPSHOT_PATH = DATA_DIR / "last_snapshot.json"
SUBSCRIPTION_CACHE = DATA_DIR / "subscription_raw.txt"

_xray_proc: subprocess.Popen[bytes] | None = None
_stop = False


def log(msg: str) -> None:
    print(f"[v2raytun] {msg}", flush=True)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def write_snapshot(payload: dict[str, Any]) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    payload = {**payload, "recordedAt": now_iso()}
    SNAPSHOT_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def detect_xray_asset() -> str:
    machine = platform.machine().lower()
    if machine in ("x86_64", "amd64"):
        return "Xray-linux-64.zip"
    if machine in ("aarch64", "arm64"):
        return "Xray-linux-arm64-v8a.zip"
    raise RuntimeError(f"Unsupported architecture: {machine}")


def download_urls(asset: str) -> list[str]:
    tag = XRAY_VERSION if XRAY_VERSION.startswith("v") else f"v{XRAY_VERSION}"
    github = f"https://github.com/XTLS/Xray-core/releases/download/{tag}/{asset}"
    return [
        github,
        f"https://ghproxy.net/{github}",
        f"https://mirror.ghproxy.com/{github}",
    ]


def ensure_xray() -> Path:
    BIN_DIR.mkdir(parents=True, exist_ok=True)
    if XRAY_BIN.is_file() and os.access(XRAY_BIN, os.X_OK):
        return XRAY_BIN

    asset = detect_xray_asset()
    zip_path = BIN_DIR / asset
    last_err: Exception | None = None
    for url in download_urls(asset):
        try:
            log(f"Downloading Xray {XRAY_VERSION}: {url}")
            req = urllib.request.Request(url, headers={"User-Agent": "wash-module-v2raytun/1.0"})
            with urllib.request.urlopen(req, timeout=120) as resp:
                zip_path.write_bytes(resp.read())
            last_err = None
            break
        except Exception as exc:  # noqa: BLE001
            last_err = exc
            log(f"Download failed: {exc}")
    if last_err is not None and not zip_path.is_file():
        raise RuntimeError(f"Cannot download Xray: {last_err}")

    with zipfile.ZipFile(zip_path, "r") as zf:
        member = next((n for n in zf.namelist() if n.endswith("xray") or n == "xray"), None)
        if not member:
            raise RuntimeError("xray binary not found in archive")
        with zf.open(member) as src, open(XRAY_BIN, "wb") as dst:
            dst.write(src.read())
    XRAY_BIN.chmod(0o755)
    try:
        zip_path.unlink(missing_ok=True)
    except OSError:
        pass
    log(f"Xray ready: {XRAY_BIN}")
    return XRAY_BIN


def b64decode_padded(raw: str) -> str:
    text = raw.strip().replace("\n", "").replace("\r", "").replace(" ", "")
    pad = (-len(text)) % 4
    if pad:
        text += "=" * pad
    return base64.urlsafe_b64decode(text.encode("ascii")).decode("utf-8", errors="replace")


def fetch_share_payload(link: str) -> str:
    if link.lower().startswith("vless://"):
        return link
    req = urllib.request.Request(
        link,
        headers={
            "User-Agent": "v2raytun/wash-module",
            "Accept": "text/plain,*/*",
        },
    )
    with urllib.request.urlopen(req, timeout=45) as resp:
        body = resp.read()
    text = body.decode("utf-8", errors="replace").strip()
    SUBSCRIPTION_CACHE.write_text(text, encoding="utf-8")
    if "vless://" in text.lower():
        return text
    try:
        decoded = b64decode_padded(text)
        if "vless://" in decoded.lower():
            return decoded
    except Exception:  # noqa: BLE001
        pass
    return text


def extract_vless_uris(payload: str) -> list[str]:
    lines: list[str] = []
    for raw in payload.replace("\r", "\n").split("\n"):
        line = raw.strip()
        if not line:
            continue
        if line.lower().startswith("vless://"):
            lines.append(line)
            continue
        # Some panels put several URIs in one line separated by spaces
        for part in line.split():
            if part.lower().startswith("vless://"):
                lines.append(part)
    # Deduplicate preserving order
    seen: set[str] = set()
    out: list[str] = []
    for uri in lines:
        if uri not in seen:
            seen.add(uri)
            out.append(uri)
    return out


def parse_vless(uri: str) -> dict[str, Any]:
    parsed = urllib.parse.urlparse(uri)
    if parsed.scheme.lower() != "vless":
        raise ValueError("Not a vless:// URI")
    uuid = urllib.parse.unquote(parsed.username or "")
    host = parsed.hostname or ""
    port = parsed.port or 443
    if not uuid or not host:
        raise ValueError("Invalid vless:// URI (missing uuid/host)")
    q = {k.lower(): v for k, v in urllib.parse.parse_qs(parsed.query, keep_blank_values=True).items()}
    def get(key: str, default: str = "") -> str:
        vals = q.get(key)
        return vals[0] if vals else default

    remark = urllib.parse.unquote(parsed.fragment or "") or f"{host}:{port}"
    network = (get("type") or get("network") or "tcp").lower()
    security = (get("security") or "none").lower()
    flow = get("flow")
    encryption = get("encryption") or "none"

    stream: dict[str, Any] = {"network": network, "security": security}
    if security in ("tls", "reality"):
        tls_block: dict[str, Any] = {
            "serverName": get("sni") or get("host") or host,
            "fingerprint": get("fp") or "chrome",
        }
        alpn = get("alpn")
        if alpn:
            tls_block["alpn"] = [p.strip() for p in alpn.split(",") if p.strip()]
        if security == "tls":
            stream["tlsSettings"] = tls_block
        else:
            reality = {
                **tls_block,
                "publicKey": get("pbk"),
                "shortId": get("sid"),
                "spiderX": get("spx") or "/",
            }
            stream["realitySettings"] = reality

    if network == "ws":
        stream["wsSettings"] = {
            "path": get("path") or "/",
            "headers": {"Host": get("host") or get("sni") or host},
        }
    elif network == "grpc":
        stream["grpcSettings"] = {
            "serviceName": get("servicename") or get("serviceName") or "",
            "multiMode": (get("mode") or "").lower() == "multi",
        }
    elif network in ("httpupgrade", "http"):
        stream["network"] = "httpupgrade"
        stream["httpupgradeSettings"] = {
            "path": get("path") or "/",
            "host": get("host") or get("sni") or host,
        }
    elif network in ("xhttp", "splithttp"):
        stream["network"] = "xhttp"
        stream["xhttpSettings"] = {
            "path": get("path") or "/",
            "host": get("host") or get("sni") or host,
            "mode": get("mode") or "auto",
        }
    elif network == "h2":
        stream["httpSettings"] = {
            "path": get("path") or "/",
            "host": [get("host") or get("sni") or host],
        }

    user: dict[str, Any] = {"id": uuid, "encryption": encryption}
    if flow:
        user["flow"] = flow

    return {
        "remark": remark,
        "address": host,
        "port": int(port),
        "user": user,
        "stream": stream,
        "uri": uri,
    }


def build_xray_config(server: dict[str, Any]) -> dict[str, Any]:
    return {
        "log": {"loglevel": "warning"},
        "inbounds": [
            {
                "tag": "socks-in",
                "listen": "0.0.0.0",
                "port": SOCKS_PORT,
                "protocol": "socks",
                "settings": {"udp": True, "auth": "noauth"},
            },
            {
                "tag": "http-in",
                "listen": "0.0.0.0",
                "port": HTTP_PORT,
                "protocol": "http",
                "settings": {"allowTransparent": False},
            },
        ],
        "outbounds": [
            {
                "tag": "proxy",
                "protocol": "vless",
                "settings": {
                    "vnext": [
                        {
                            "address": server["address"],
                            "port": server["port"],
                            "users": [server["user"]],
                        }
                    ]
                },
                "streamSettings": server["stream"],
            },
            {"tag": "direct", "protocol": "freedom"},
            {"tag": "block", "protocol": "blackhole"},
        ],
        "routing": {
            "domainStrategy": "AsIs",
            "rules": [
                {"type": "field", "outboundTag": "proxy", "network": "tcp,udp"},
            ],
        },
    }


def stop_xray() -> None:
    global _xray_proc
    proc = _xray_proc
    _xray_proc = None
    if not proc:
        return
    if proc.poll() is None:
        log("Stopping Xray…")
        try:
            proc.send_signal(signal.SIGTERM)
            proc.wait(timeout=8)
        except Exception:  # noqa: BLE001
            try:
                proc.kill()
            except Exception:  # noqa: BLE001
                pass


def start_xray(config: dict[str, Any]) -> None:
    global _xray_proc
    stop_xray()
    CONFIG_PATH.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")
    ensure_xray()
    log(f"Starting Xray socks={SOCKS_PORT} http={HTTP_PORT}")
    _xray_proc = subprocess.Popen(
        [str(XRAY_BIN), "run", "-c", str(CONFIG_PATH)],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        cwd=str(DATA_DIR),
    )
    time.sleep(1.2)
    if _xray_proc.poll() is not None:
        out = b""
        if _xray_proc.stdout:
            try:
                out = _xray_proc.stdout.read() or b""
            except Exception:  # noqa: BLE001
                out = b""
        raise RuntimeError(f"Xray exited early: {out.decode('utf-8', errors='replace')[:500]}")


def port_open(port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=2):
            return True
    except OSError:
        return False


def healthcheck_via_http_proxy() -> tuple[bool, str]:
    proxy = f"http://127.0.0.1:{HTTP_PORT}"
    handlers = urllib.request.ProxyHandler({"http": proxy, "https": proxy})
    opener = urllib.request.build_opener(handlers)
    req = urllib.request.Request(
        HEALTHCHECK_URL,
        headers={"User-Agent": "wash-module-v2raytun-health/1.0"},
        method="GET",
    )
    try:
        with opener.open(req, timeout=20) as resp:
            code = getattr(resp, "status", None) or resp.getcode()
            return True, f"HTTP {code}"
    except urllib.error.HTTPError as exc:
        # Some check endpoints return 204/404 — still means tunnel works
        if 200 <= exc.code < 500:
            return True, f"HTTP {exc.code}"
        return False, f"HTTP {exc.code}"
    except Exception as exc:  # noqa: BLE001
        return False, str(exc)


def select_server(uris: list[str]) -> dict[str, Any]:
    if not uris:
        raise RuntimeError("No vless:// servers found in link/subscription")
    idx = max(0, min(SERVER_INDEX, len(uris) - 1))
    server = parse_vless(uris[idx])
    server["index"] = idx
    server["total"] = len(uris)
    return server


def resolve_and_apply() -> dict[str, Any]:
    if not SHARE_LINK:
        raise RuntimeError("share_link_secret is empty — paste subscription or vless:// link in settings")
    payload = fetch_share_payload(SHARE_LINK)
    uris = extract_vless_uris(payload)
    server = select_server(uris)
    config = build_xray_config(server)
    start_xray(config)
    return {
        "ok": True,
        "phase": "running",
        "remark": server["remark"],
        "address": server["address"],
        "port": server["port"],
        "serverIndex": server["index"],
        "serverTotal": server["total"],
        "socksPort": SOCKS_PORT,
        "httpPort": HTTP_PORT,
        "proxyHint": f"HTTP_PROXY=http://pyorch-runtime:{HTTP_PORT}",
        "socksHint": f"ALL_PROXY=socks5://pyorch-runtime:{SOCKS_PORT}",
        "xrayVersion": XRAY_VERSION,
        "health": {"ok": False, "detail": "pending"},
        "error": None,
    }


def handle_signal(signum: int, _frame: Any) -> None:
    global _stop
    log(f"Signal {signum}, shutting down")
    _stop = True


def main() -> None:
    signal.signal(signal.SIGTERM, handle_signal)
    signal.signal(signal.SIGINT, handle_signal)

    state: dict[str, Any] = {
        "ok": False,
        "phase": "starting",
        "error": None,
        "socksPort": SOCKS_PORT,
        "httpPort": HTTP_PORT,
    }
    write_snapshot(state)

    last_refresh = 0.0
    last_health = 0.0

    while not _stop:
        try:
            need_refresh = (_xray_proc is None) or (time.time() - last_refresh >= max(300, REFRESH_INTERVAL))
            if need_refresh:
                state = resolve_and_apply()
                last_refresh = time.time()
                write_snapshot(state)
                log(
                    f"Connected remark={state.get('remark')} "
                    f"server={state.get('address')}:{state.get('port')} "
                    f"({state.get('serverIndex')}+1/{state.get('serverTotal')})"
                )

            if _xray_proc and _xray_proc.poll() is not None:
                out = b""
                if _xray_proc.stdout:
                    try:
                        out = _xray_proc.stdout.read() or b""
                    except Exception:  # noqa: BLE001
                        out = b""
                raise RuntimeError(
                    f"Xray process died: {out.decode('utf-8', errors='replace')[:400]}"
                )

            if time.time() - last_health >= max(15, HEALTHCHECK_INTERVAL):
                socks_ok = port_open(SOCKS_PORT)
                http_ok = port_open(HTTP_PORT)
                ok, detail = (False, "ports closed")
                if http_ok:
                    ok, detail = healthcheck_via_http_proxy()
                state["health"] = {
                    "ok": ok,
                    "detail": detail,
                    "socksListening": socks_ok,
                    "httpListening": http_ok,
                    "checkedAt": now_iso(),
                }
                state["ok"] = bool(ok and socks_ok and http_ok)
                state["phase"] = "healthy" if state["ok"] else "degraded"
                state["error"] = None if state["ok"] else detail
                write_snapshot(state)
                log(f"Health ok={ok} detail={detail}")
                last_health = time.time()

        except Exception as exc:  # noqa: BLE001
            log(f"Error: {exc}")
            state = {
                **state,
                "ok": False,
                "phase": "error",
                "error": str(exc),
                "socksPort": SOCKS_PORT,
                "httpPort": HTTP_PORT,
            }
            write_snapshot(state)
            stop_xray()
            # Back off then retry
            for _ in range(15):
                if _stop:
                    break
                time.sleep(1)
            last_refresh = 0.0
            continue

        for _ in range(5):
            if _stop:
                break
            time.sleep(1)

    stop_xray()
    write_snapshot({**state, "phase": "stopped", "ok": False})
    log("Stopped")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:  # noqa: BLE001
        log(f"Fatal: {exc}")
        write_snapshot({"ok": False, "phase": "fatal", "error": str(exc)})
        stop_xray()
        sys.exit(1)
