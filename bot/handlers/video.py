import os
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

MAX_FILE_SIZE = 50 * 1024 * 1024  # 50MB

def build_action_keyboard(file_id: str, file_type: str):
    keyboard = [
        [
            InlineKeyboardButton("✂️ Trim", callback_data=f"trim|{file_id}|{file_type}"),
            InlineKeyboardButton("🗜️ Compress", callback_data=f"compress|{file_id}|{file_type}"),
        ],
        [
            InlineKeyboardButton("🎵 Ekstrak Audio", callback_data=f"audio|{file_id}|{file_type}"),
            InlineKeyboardButton("🔄 Convert MP4", callback_data=f"convert|{file_id}|{file_type}"),
        ],
    ]
    return InlineKeyboardMarkup(keyboard)

async def video_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.message
    video = message.video or message.document

    if not video:
        await message.reply_text("❌ File tidak dikenali.")
        return

    if video.file_size and video.file_size > MAX_FILE_SIZE:
        await message.reply_text(
            f"❌ File terlalu besar! Maks 50MB.\n"
            f"Ukuran file kamu: {video.file_size / 1024 / 1024:.1f}MB"
        )
        return

    file_id = video.file_id
    keyboard = build_action_keyboard(file_id, "video")

    await message.reply_text(
        "🎬 *Video diterima!*\n\nPilih aksi yang ingin dilakukan:",
        reply_markup=keyboard,
        parse_mode="Markdown"
    )

async def audio_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.message
    audio = message.audio or message.document

    if not audio:
        await message.reply_text("❌ File tidak dikenali.")
        return

    if audio.file_size and audio.file_size > MAX_FILE_SIZE:
        await message.reply_text("❌ File terlalu besar! Maks 50MB.")
        return

    file_id = audio.file_id
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("🔄 Convert ke MP3", callback_data=f"to_mp3|{file_id}|audio")]
    ])

    await message.reply_text(
        "🎵 *Audio diterima!*\n\nPilih aksi:",
        reply_markup=keyboard,
        parse_mode="Markdown"
    )
