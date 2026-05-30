from telegram import Update
from telegram.ext import ContextTypes

WELCOME_TEXT = """
🎬 *Selamat datang di Clips Factory Bot!*

Bot ini bisa memproses video dan audio kamu langsung di Telegram.

*Fitur yang tersedia:*
✂️ Trim / potong video
🗜️ Compress video
🎵 Ekstrak audio dari video
🔄 Convert format video

*Cara pakai:*
Cukup kirim video atau file audio ke bot ini, lalu pilih aksi yang kamu inginkan.

Ketik /help untuk bantuan lebih lanjut.
"""

HELP_TEXT = """
📖 *Panduan Penggunaan*

1️⃣ Kirim video (maks 50MB via Telegram)
2️⃣ Pilih aksi dari tombol yang muncul:
   - ✂️ *Trim* → potong bagian video
   - 🗜️ *Compress* → perkecil ukuran file
   - 🎵 *Audio* → ekstrak jadi MP3
   - 🔄 *Convert* → ubah ke MP4

3️⃣ Ikuti instruksi bot
4️⃣ Hasil dikirim balik ke kamu!

⚠️ *Batasan:*
- Ukuran file maks: 50MB
- Format didukung: MP4, MKV, AVI, MOV, WebM
"""

async def start_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(WELCOME_TEXT, parse_mode="Markdown")

async def help_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(HELP_TEXT, parse_mode="Markdown")
