# wash-module-v2raytun

Модуль VLESS-клиента для [WASH PRO CRM](https://github.com/WASH-PRO/WASH-PRO-CRM).

Запускает **Xray-core** как daemon в PyOrchestrator: вставьте URL подписки или ссылку `vless://`, получите локальные прокси **SOCKS5** и **HTTP**, проверки канала и логи в Dashboard.

## Настройки

| Ключ | Описание |
|------|----------|
| `share_link_secret` | URL подписки или сырой `vless://` |
| `socks_port` | Порт SOCKS5 (по умолчанию `10808`) |
| `http_port` | Порт HTTP-прокси (по умолчанию `10809`) |
| `server_index` | Какой VLESS брать из подписки (с 0) |
| `refresh_interval` | Обновление подписки (сек) |
| `healthcheck_interval` | Проверка через HTTP-прокси (сек) |
| `healthcheck_url` | URL для проверки |
| `xray_version` | Тег релиза XTLS/Xray-core |

## Прокси из других контейнеров

```bash
HTTP_PROXY=http://pyorch-runtime:10809
HTTPS_PROXY=http://pyorch-runtime:10809
ALL_PROXY=socks5://pyorch-runtime:10808
```

## Установка

1. Dashboard → Модули → Обновить каталог
2. Установить **Клиент v2RayTun (VLESS)**
3. Вставить ссылку → Сохранить → Запустить

Нужен включённый PyOrchestrator (`PYORCHESTRATOR_ENABLED=true`).

EN: [README.md](./README.md)

---

**Сайт:** [wash-pro.tech](https://wash-pro.tech) · **GitHub:** [WASH-PRO](https://github.com/WASH-PRO)
