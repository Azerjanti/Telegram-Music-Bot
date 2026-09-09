import os

from telegram.ext import ApplicationBuilder

from music_bot.config import load_dotenv_file
from music_bot.main import initialize, register_handlers, shutdown

# A local .env file (BOT_TOKEN, ADMIN_ID, ...) is picked up automatically;
# real environment variables always take priority.
load_dotenv_file()

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
