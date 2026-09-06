# Telegram Music Bot

Асинхронный Telegram-бот для поиска музыки, кэширования отправленных треков по Telegram `file_id`, локального топа и распознавания голосовых сообщений.

## Что уже работает

- `/start` с русской клавиатурой.
- Поиск по локальному каталогу с fuzzy matching от 70%.
- Загрузка через отдельный `yt-dlp` адаптер, повтор до 3 раз и конвертация в MP3 192 kbps через ffmpeg.
- Сохранение `file_id` и счётчика прослушиваний.
- «Песни исполнителя», локальный топ до 50 треков и inline-кнопки.
- «Топ 100» через Spotify Global Top 50 при наличии Spotify Client ID/Secret; без них используется локальный fallback.
- Распознавание `voice`/`audio` через `shazamio`, если пакет включён.
- `GET /health` для мониторинга.
- PostgreSQL через `DATABASE_URL` с локальным SQLite fallback.

## Запуск

```bash
pip install -r music_bot/requirements.txt
export BOT_TOKEN="..."
python -m music_bot.main
```

В Replit токен хранится в Secrets. Для Render задайте `BOT_TOKEN` и `DATABASE_URL` в настройках сервиса; пример `render.yaml` уже добавлен.
Для Spotify Top 100 добавьте `SPOTIFY_CLIENT_ID` и `SPOTIFY_CLIENT_SECRET`; эти значения также должны храниться в Secrets.

## Источник аудио и права

`ENABLE_YTDLP_DOWNLOADS` включён для совместимости с исходным ТЗ, но использовать загрузчик нужно только для аудио, на которое у владельца бота есть права или разрешение источника. Для каталога с лицензированной музыкой замените `YouTubeProvider` на адаптер вашего разрешённого провайдера, не меняя handlers и базу.