# Telegram Habit Bot

Трекер привычек в Telegram: пользователи добавляют привычки, отмечают выполнение, получают напоминания и статистику. Владелец видит статистику по всем пользователям.

## Команды

| Команда | Описание |
|---|---|
| `/start` | Регистрация и справка |
| `/add 🏃 Бег` | Добавить привычку (эмодзи необязателен) |
| `/habits` | Список привычек на сегодня |
| `/done 1` | Отметить выполнение (номер, название или `all`) |
| `/skip 1` | Пропустить день без разрыва стрика |
| `/del 1` | Удалить привычку |
| `/stats` | Стрики и прогресс |
| `/remind 20:00` | Напоминание в это время (`/remind off` — выключить) |
| `/adminstats` | Статистика по всем пользователям (только владелец) |

## Запуск

```
pip install -r requirements.txt
copy .env.example .env   # впиши BOT_TOKEN и ADMIN_ID
python bot.py
```

- `BOT_TOKEN` — от @BotFather (`/newbot`)
- `ADMIN_ID` — твой Telegram id, узнать можно у @userinfobot. Без него `/adminstats` не работает.

Данные хранятся в `habits.db` (SQLite, рядом с ботом). Напоминания работают, пока запущен процесс.

## Запуск 24/7 на Oracle Cloud (бесплатно)

На любом VPS бот работает так же, только процесс не выключается. Бот ходит наружу (к Telegram), поэтому открывать порты не нужно — только SSH.

1. Зарегистрируйся на cloud.oracle.com → Create VM instance (Always Free: AMD 1 OCPU/1GB или ARM 4 OCPU/24GB).
2. Установи Docker:
   ```
   sudo apt update && sudo apt install -y docker.io docker-compose-v2
   sudo systemctl enable --now docker
   ```
3. Скопируй проект на сервер (`git clone` или закинь по SCP) и зайди в папку.
4. Создай `.env` и впиши токен и свой id:
   ```
   cp .env.example .env
   nano .env
   ```
5. Запусти:
   ```
   sudo docker compose up -d --build
   ```
6. Проверь логи: `sudo docker compose logs -f`

База лежит в docker volume `habit_bot_data` и переживает перезапуски и пересборки. Чтобы бот поднялся сам после ребута сервера — в docker-compose уже стоит `restart: unless-stopped`.

Полезно: `sudo docker compose logs --tail 50` — логи, `sudo docker compose restart` — перезапуск.

## Часовой пояс

По умолчанию `+03:00` для всех пользователей (как в веб-версии habit-tracker).