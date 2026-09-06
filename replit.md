# Telegram Music Bot

Асинхронный русскоязычный Telegram-бот для поиска, кэширования и воспроизведения музыкальных треков.

## Run & Operate

- `python -m music_bot.main` — run the Telegram bot and health server
- `pnpm --filter @workspace/api-server run dev` — run the shared API server (port 5000)
- `pnpm run typecheck` — full typecheck across all packages
- `pnpm run build` — typecheck + build all packages
- `pnpm --filter @workspace/api-spec run codegen` — regenerate API hooks and Zod schemas from the OpenAPI spec
- `pnpm --filter @workspace/db run push` — push DB schema changes (dev only)
- Required secret: `BOT_TOKEN`
- Optional env: `DATABASE_URL` for PostgreSQL; without it the bot uses a local SQLite file in `/tmp`

## Stack

- pnpm workspaces, Node.js 24, TypeScript 5.9
- API: Express 5
- DB: PostgreSQL + Drizzle ORM
- Validation: Zod (`zod/v4`), `drizzle-zod`
- API codegen: Orval (from OpenAPI spec)
- Build: esbuild (CJS bundle)

## Where things live

- `music_bot/main.py` — lifecycle, polling and health server
- `music_bot/handlers/` — Telegram command, text, callback and voice handlers
- `music_bot/database/db.py` — PostgreSQL/SQLite persistence and fuzzy lookup
- `music_bot/downloader/youtube.py` — replaceable audio provider adapter
- `music_bot/services/music.py` — cache-first send/download flow

## Architecture decisions

- The downloader is an adapter and can be replaced with a licensed catalog without changing bot handlers.
- Telegram `file_id` is the cache key after the first successful send; audio files are removed from local disk afterward.
- `DATABASE_URL` selects PostgreSQL; local development falls back to SQLite in `/tmp` so the bot can boot without provisioning a database.

## Product

- Russian-language search, artist lookup, local popularity top, inline actions and optional voice recognition.

## User preferences

_Populate as you build — explicit user instructions worth remembering across sessions._

## Gotchas

- `ffmpeg` must be available when `ENABLE_YTDLP_DOWNLOADS=true`.
- Use the downloader only for content that the bot owner is authorized to distribute.

## Pointers

- See the `pnpm-workspace` skill for workspace structure, TypeScript setup, and package details
