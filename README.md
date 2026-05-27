# Інтервальне голодування — Telegram Mini App

Pure-vanilla (HTML + JS + CSS, zero dependencies) Telegram Mini App для відстеження інтервального голодування.

## Що працює

- 4 протоколи: 16:8, 18:6, 20:4, OMAD (23:1) + власний інтервал
- Активний таймер (counts up до ціль + час понад)
- Прогрес-кільце + лінійний прогрес-бар
- Історія (останні 100 голодувань)
- Статистика: всього, найдовше, середнє, поточна серія днів
- Native Telegram theme (підлаштовується під dark/light користувача)
- Haptic feedback на iOS/Android клієнтах
- Працює і поза Telegram'ом (fallback на localStorage) — зручно для розробки

## Storage

| Где | Що | Sync між девайсами |
|---|---|---|
| Telegram CloudStorage | історія + активний fast | ✅ автоматично |
| localStorage (fallback) | те саме | ❌ тільки локально |

Нема власного сервера. Нема бази даних. Нема витрат.

## Стек

- HTML / CSS / vanilla JS — нуль dependencies, нуль build step
- Telegram WebApp SDK (підключено через CDN: `https://telegram.org/js/telegram-web-app.js`)
- Static hosting: GitHub Pages / Cloudflare Pages / Vercel — все безкоштовно

## Налаштування (3 кроки)

### 1. Створити бота через @BotFather

У Telegram:
```
/start  (до @BotFather)
/newbot
<твоє ім'я бота>
<username бота, має закінчуватись на _bot або Bot>
```

Зберігаєш отриманий `BOT_TOKEN` (на майбутнє якщо буде server-side частина для нагадувань).

### 2. Hosting (вибрати один)

**Cloudflare Pages (рекомендовано):**
1. Залогінитись на dash.cloudflare.com
2. Workers & Pages → Create → Pages → Connect to Git
3. Вибрати цей repo (`rossanoua/fasting-app`)
4. Build command: _(порожньо)_
5. Output directory: `/`
6. Deploy → отримуєш URL типу `fasting-app.pages.dev`

**Або GitHub Pages:**
1. Settings → Pages → Source: `main` branch, root
2. Чекати ~1 хв → URL `https://rossanoua.github.io/fasting-app/`

### 3. Привʼязати Mini App до бота

У @BotFather:
```
/newapp
<вибрати свого бота>
<title> — наприклад: Інтервальне голодування
<short description> — Таймер інтервального голодування
<photo 640×360> — будь-який значок
<demo GIF> — skip
<short name> — fast (буде у URL: t.me/<твій_бот>/fast)
<Web App URL> — URL з кроку 2 (https://fasting-app.pages.dev)
```

Тепер відкривається через `t.me/<твій_бот>/fast` або через кнопку у боті.

### 4. (Optional) Додати кнопку у меню бота

У @BotFather:
```
/mybots → <бот> → Bot Settings → Menu Button → Configure menu button
<button text> — Голодування
<URL> — https://fasting-app.pages.dev
```

Тепер у чаті з ботом зʼявляється синя кнопка внизу.

## Локальна розробка

```bash
cd fasting-app
python3 -m http.server 8000
# відкрити http://localhost:8000 у браузері
```

Поза Telegram'ом працює через localStorage fallback — всі функції доступні крім CloudStorage sync між девайсами.

## Структура

```
fasting-app/
├── index.html         # розмітка
├── app.js             # логіка (таймер, storage, render)
├── styles.css         # стилі з Telegram theme variables
└── README.md          # цей файл
```

Всього ~600 рядків коду. Без build step. Без npm.

## Roadmap (ідеї для наступних версій)

- [ ] Server-side bot для push-нагадувань (Cloudflare Workers + cron) — нагадати коли вікно голодування закінчилось / починається
- [ ] Графік прогресу за тиждень/місяць (Chart.js або canvas)
- [ ] Експорт історії в CSV
- [ ] Share-message — поділитися streak'ом у чаті через `shareMessage()` API
- [ ] Більше протоколів (5:2, Eat-Stop-Eat, тощо)
- [ ] Вода / ваги tracking паралельно з fasting
