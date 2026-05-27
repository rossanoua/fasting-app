# Fasting reminder bot

Telegram bot що нагадує:
- 🎯 коли голодування завершено (можна їсти)
- ⏰ коли вікно їжі зачинилось (пора голодувати знову)

Працює паралельно з Mini App (візуальний таймер). Mini App = візуальний UI, bot = напомінання.

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
- `/help` — довідка

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

### 3. Запуск

```bash
docker compose up -d --build
# Перевір логи:
docker compose logs -f fasting-bot
```

Має побачити `scheduler started, polling Telegram...`.

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

- [ ] Unified state з Mini App (Mini App POST'ить у bot HTTPS endpoint при старті) — щоб не треба було стартувати таймер двічі
- [ ] Stats: weekly summary "🏆 5 голодувань цього тижня, серія 5 днів"
- [ ] Кастомні нагадування ("за 30 хв до завершення")
- [ ] Multi-user аналітика для адміна (просто `/admin stats`)
