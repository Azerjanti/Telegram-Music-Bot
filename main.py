import os

from telegram.ext import ApplicationBuilder

from music_bot.main import initialize, register_handlers, shutdown

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()

if __name__ == "__main__":
    app = (
        ApplicationBuilder()
        .token(BOT_TOKEN)
        .post_init(initialize)
        .post_shutdown(shutdown)
        .build()
    )
    register_handlers(app)
    app.run_polling(drop_pending_updates=True)
