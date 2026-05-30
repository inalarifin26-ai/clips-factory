import logging
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, filters
from bot.handlers.start import start_handler, help_handler
from bot.handlers.video import video_handler, audio_handler
from bot.handlers.callbacks import callback_handler
from bot.handlers.cs import cs_handler, manual_handler, clear_history_handler
from dotenv import load_dotenv
import os

load_dotenv()

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

def main():
    token = os.getenv("BOT_TOKEN")
    if not token:
        raise ValueError("BOT_TOKEN tidak ditemukan di .env!")

    app = Application.builder().token(token).build()

    # Command handlers
    app.add_handler(CommandHandler("start", start_handler))
    app.add_handler(CommandHandler("help", help_handler))
    app.add_handler(CommandHandler("manual", manual_handler))
    app.add_handler(CommandHandler("reset", clear_history_handler))

    # Media handlers
    app.add_handler(MessageHandler(filters.VIDEO | filters.Document.VIDEO, video_handler))
    app.add_handler(MessageHandler(filters.AUDIO | filters.Document.AUDIO, audio_handler))

    # Callback handler (inline buttons)
    app.add_handler(CallbackQueryHandler(callback_handler))

    # CS Handler — teks biasa (private + group, harus paling bawah)
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, cs_handler))

    logger.info("ClipsUp AI Bot berjalan...")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
