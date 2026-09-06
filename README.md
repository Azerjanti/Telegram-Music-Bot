# Telegram Music Bot

Асинхронный Telegram-бот для поиска музыки, кэширования отправленных треков по Telegram `file_id`, локального топа и распознавания голосовых сообщений.

## Что уже работает

- `/start` с русской клавиатурой.
- Поиск по локальному каталогу с fuzzy matching от 70%.
- Загрузка через отдельный `yt-dlp` адаптер, повтор до 3 раз и конвертация в MP3 192 kbps через ffmpeg.
- Сохранение `file_id` и счётчика прослушиваний.
- «Песни исполнителя», локальный топ до 50 треков и inline-кнопки.
- Избранное через ❤️, команду `/like` и кнопку «Избранное».
- «Топ 100» через Spotify Global Top 50 при наличии Spotify Client ID/Secret; без них используется локальный fallback.
- Распознавание `voice`, `audio` и аудиодокументов через `ffmpeg` и `shazamio`.
- `GET /health` для мониторинга.
- Supabase cache через `SUPABASE_URL`/`SUPABASE_KEY`, PostgreSQL через `DATABASE_URL` и локальный SQLite fallback.

## Запуск

```bash
pip install -r requirements.txt
export BOT_TOKEN="..."
python -m music_bot.main
```

В Replit токен хранится в Secrets. Для Render задайте `BOT_TOKEN`, `SPOTIFY_CLIENT_ID`, `SPOTIFY_CLIENT_SECRET`, `SUPABASE_URL` и `SUPABASE_KEY` в настройках сервиса; пример `render.yaml` уже добавлен.
Для Spotify Top 100 добавьте `SPOTIFY_CLIENT_ID` и `SPOTIFY_CLIENT_SECRET`; эти значения также должны храниться в Secrets.
Для Supabase сначала выполните `supabase/schema.sql` в SQL Editor проекта Supabase. Если таблица недоступна, бот явно пишет причину в лог и временно использует локальное хранилище в `/tmp`.

## Render

- Build command: `pip install -r requirements.txt`
- Start command: `python main.py`
- Worker configuration: `render.yaml` или `Procfile`
Скрипт создаёт таблицы `songs` и `favorites`; после изменения схемы его нужно повторно выполнить в SQL Editor.

## Источник аудио и права

`ENABLE_YTDLP_DOWNLOADS` включён для совместимости с исходным ТЗ, но использовать загрузчик нужно только для аудио, на которое у владельца бота есть права или разрешение источника. Для каталога с лицензированной музыкой замените `YouTubeProvider` на адаптер вашего разрешённого провайдера, не меняя handlers и базу.