# Fasting reminder bot

Telegram bot що нагадує:
- 🎯 коли голодування завершено (можна їсти)
- ⏰ коли вікно їжі зачинилось (пора голодувати знову)

Працює паралельно з Mini App (візуальний таймер). Mini App = візуальний UI, bot = нагадування.

## Стек

- Python 3.12 + [aiogram 3](https://docs.aiogram.dev/)
- SQLite (через aiosqlite) — per-user state
- APScheduler — background tick кожні 60 с для перевірки due notifications
- Docker з `restart: always` — постійний polling

## Команди бота

- `/start` — обрати протокол через inline-кнопки
- `/16 /18 /20 /omad` — стартонути напряму
- `/status` — поточний стан + час що лишився
- `/stop` — припинити поточний таймер
- `/can` — що можна/не можна під час голодування
- `/donate` — підтримати розробку (Telegram Stars)
- `/help` — довідка

## Mini App ↔ Bot sync

Коли користувач тисне 16:8 у Mini App, він POST'ить через Cloudflare Tunnel
на `https://<your-tunnel>/api/sync` з підписаним `initData` (HMAC-SHA256).
Бот валідує підпис, стартує таймер у SQLite, планує нагадування.

Це означає що **користувач тисне один раз** (у Mini App або у боті — байдуже),
і нагадування приходять автоматично.

## Setup (5 хв)

### 1. BOT_TOKEN

Якщо ще не зробив — @BotFather → `/newbot` → username `_bot`. Скопіюй токен.

(Для існуючого `@fasting_tracker_ua_bot`: @BotFather → `/token` → вибрати бота.)

### 2. Конфіг

```bash
cd ~/projects/fasting-app/bot
cp .env.example .env
# Відкрий .env, встав свій BOT_TOKEN
```

### 3. Cloudflare Tunnel setup (для Mini App sync)

Потрібно один раз створити tunnel у Cloudflare щоб Mini App міг достукатись до бота через HTTPS.

**3.1. Зайди на https://one.dash.cloudflare.com/ → Networks → Tunnels → Create a tunnel**

- Tunnel type: Cloudflared
- Name: `fasting-bot` (або будь-яке)
- Save → скопіюй **Tunnel token** (довгий рядок eyJ...)

**3.2. У Public Hostname секції:**
- Subdomain: будь-який (напр. `fasting-bot`)
- Domain: твій CF-домен АБО `*.trycloudflare.com` (free, без власного домену)
- Type: HTTP
- URL: `fasting-bot:8080`  ← це docker service name + port

**3.3. Запиши token у .env:**
```
TUNNEL_TOKEN=eyJ...весь_token_сюди...
```

**3.4. Запам'ятай публічний URL** (типу `https://fasting-bot.example.com` або `https://abc-xyz-123.trycloudflare.com`) — потім вставимо у Mini App конфіг.

### 4. Запуск бота + tunnel

```bash
docker compose up -d --build
# Перевір логи обох сервісів:
docker compose logs -f
```

Має побачити:
- `fasting-bot ... sync API on 0.0.0.0:8080`
- `fasting-bot ... scheduler started, polling Telegram...`
- `cloudflared ... Registered tunnel connection`

### 5. Налаштування Mini App для sync

Відкрий `app.js` у repo root → знайди `CONFIG.botSyncUrl` → встав свій публічний URL з кроку 3.4 → закомить + push. GitHub Pages підхопить за ~1 хв.

Без цього кроку Mini App працює standalone (CloudStorage), але без нагадувань — бо не знає куди POST'ити.

### 6. Smoke test

- Відкрий `https://<your-tunnel-url>/health` у браузері → має відповісти `{"ok":true}`
- У Telegram → бот → `/start` → натисни 16:8 → отримуєш `✅ Старт...`
- Mini App: відкрий → натисни 16:8 → під таймером має зʼявитись `🔔 Нагадування активні`

### 4. Тест

У Telegram напиши `/start` своєму боту → з'явиться inline-клавіатура → натисни 16:8 → отримуєш повідомлення про старт.

`/status` показує поточний стан. Через 16 год (або скільки виставив) прийде нотифікація.

## Реальне тестування за хвилину (без чекання 16 год)

Якщо хочеш перевірити що нотифікації спрацьовують швидко — додай тестовий протокол до `bot.py:35`:

```python
PROTOCOLS = {
    "test": (0, 0, "TEST — 1 хвилина голодування + 1 хвилина вікно"),
    ...
}
```

Потім додай у `db.py` логіку щоб target_hours * 3600 → seconds замість hours. Або просто змінити константу `60` у scheduler у `bot.py:206` до `5` щоб тики частіше йшли.

## Управління

```bash
# Перезапустити
docker compose restart

# Зупинити
docker compose down

# Подивитись чи живий
docker compose ps

# Подивитись DB вручну
sqlite3 data/fasting.db "SELECT * FROM users;"

# Очистити чийсь state (debug)
sqlite3 data/fasting.db "DELETE FROM users WHERE user_id=YOUR_TG_ID;"
```

## Як це працює архітектурно

```
┌─────────┐     /16          ┌──────────┐    INSERT/UPDATE   ┌──────────┐
│  User   │ ───────────────> │   bot.py │ ─────────────────> │ SQLite   │
│ Telegram│                  │ aiogram  │                    │ users    │
└─────────┘ <──────────────  │ handlers │                    └──────────┘
              ✅ старт ОК     └──────────┘                          ▲
                                    ▲                               │
                                    │                               │ SELECT WHERE
                                    │                               │  fast_start_ts < now - target
                                    │                               │
                                    │ sendMessage                   │
                                    │ "🎯 голодування завершено"   │
                                    │                               │
                              ┌─────┴────────┐                      │
                              │ APScheduler  │──────────────────────┘
                              │ every 60 s   │
                              └──────────────┘
```

State machine на одного user'а:

```
[idle] ─/16─> [fasting] ─ tick? elapsed >= 16h ─> [eating window]
                                                         │
[idle] <─ tick? elapsed >= 8h ─────────────────────────  ┘
   ▲
   └── /stop manually anytime
```

## Roadmap

- [x] **Unified state з Mini App** — done. Mini App POST'ить у `/api/sync` через Cloudflare Tunnel.
- [x] **Telegram Stars donate** — `/donate` команда + inline buttons + автоматичний "дякую" при successful_payment.
- [ ] Stats: weekly summary "🏆 5 голодувань цього тижня, серія 5 днів"
- [ ] Кастомні нагадування ("за 30 хв до завершення")
- [ ] Підтримка кастомних протоколів (не тільки 16/18/20/omad)
- [ ] Multi-user аналітика для адміна (просто `/admin stats`)
