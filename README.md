# wash-module-v2raytun

VLESS client module for [WASH PRO CRM](https://github.com/WASH-PRO/WASH-PRO-CRM).

Runs **Xray-core** as a daemon in PyOrchestrator: paste a subscription URL or `vless://` link, get local **SOCKS5** + **HTTP** proxies, health checks, and logs in the Dashboard.

## Settings

| Key | Description |
|-----|-------------|
| `share_link_secret` | Subscription URL or raw `vless://` |
| `socks_port` | SOCKS5 listen port (default `10808`) |
| `http_port` | HTTP proxy port (default `10809`) |
| `server_index` | Which VLESS entry to use (0-based) |
| `refresh_interval` | Re-fetch subscription (seconds) |
| `healthcheck_interval` | Probe via HTTP proxy (seconds) |
| `healthcheck_url` | URL checked through the proxy |
| `xray_version` | XTLS/Xray-core release tag |

## Proxy from other containers

```bash
HTTP_PROXY=http://pyorch-runtime:10809
HTTPS_PROXY=http://pyorch-runtime:10809
ALL_PROXY=socks5://pyorch-runtime:10808
```

## Layout

| Path | Role |
|------|------|
| `wash-module.json` | Manifest / settings schema |
| `src/main.py` | Daemon |
| `ui/` | Overview / Settings / Logs + help |
| `assets/icon.svg` | Catalog icon |

## Install

1. Dashboard → Modules → Refresh catalog
2. Install **v2RayTun Client (VLESS)**
3. Paste link → Save → Start

Requires PyOrchestrator enabled (`PYORCHESTRATOR_ENABLED=true`).

RU: [README.ru.md](./README.ru.md)
