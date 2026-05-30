import os
import json
import subprocess
import tempfile
import logging
from openai import AsyncOpenAI
from telegram import Update
from telegram.ext import ContextTypes

logger = logging.getLogger(__name__)
client = AsyncOpenAI(api_key=os.getenv("OPENAI_API_KEY"))

SYSTEM_PROMPT = """Kamu adalah asisten editor video profesional. 
User akan memberikan instruksi dalam bahasa Indonesia atau Inggris tentang apa yang ingin dilakukan pada video mereka.

Tugasmu adalah menganalisis instruksi dan menghasilkan JSON dengan parameter yang tepat.

Format JSON yang harus dikembalikan (hanya JSON, tanpa teks lain):
{
  "action": "trim" | "compress" | "audio" | "convert" | "short916",
  "params": {
    "start": "00:00:00",        // untuk trim: waktu mulai (HH:MM:SS)
    "duration": "30",           // untuk trim: durasi dalam detik
    "crf": 28,                  // untuk compress: kualitas (18-35, makin besar makin kecil)
    "format": "fill"            // untuk short916: "fill", "blur", "black", "white", "face"
  },
  "explanation": "Penjelasan singkat aksi yang akan dilakukan"
}

Contoh instruksi dan output:
- "potong 30 detik pertama" → action: trim, start: 00:00:00, duration: 30
- "ambil menit ke 1 sampai 2" → action: trim, start: 00:01:00, duration: 60
- "kompres jadi lebih kecil" → action: compress, crf: 28
- "ekstrak audio" → action: audio
- "ubah ke mp4" → action: convert
- "buat jadi reels/shorts/tiktok" → action: short916, format: blur
- "buat vertical dengan blur background" → action: short916, format: blur
- "crop jadi 9:16" → action: short916, format: fill
- "cari momen lucu" → action: trim, start: 00:00:00, duration: 30 (ambil awal sebagai default)
- "buat clip pendek" → action: trim, start: 00:00:00, duration: 60

Selalu kembalikan JSON yang valid saja."""

DOWNLOAD_DIR = tempfile.gettempdir()

async def ai_prompt_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Dipanggil ketika user klik tombol AI Prompt"""
    query = update.callback_query
    if query:
        await query.answer()
        file_key = query.data.split("|")[1] if "|" in query.data else None
        if file_key:
            context.user_data["ai_pending_file"] = file_key
        await query.edit_message_text(
            "🤖 *AI Prompt Aktif*\n\n"
            "Ketik instruksi video kamu dalam bahasa bebas, contoh:\n\n"
            "• _potong 30 detik pertama_\n"
            "• _ambil menit ke 1 sampai menit ke 2_\n"
            "• _kompres jadi lebih kecil_\n"
            "• _buat jadi format reels/shorts/tiktok_\n"
            "• _ekstrak audionya saja_\n"
            "• _buat clip pendek bagian menarik_\n\n"
            "Ketik /batal untuk membatalkan.",
            parse_mode="Markdown"
        )
        context.user_data["state"] = "waiting_ai_prompt"
    else:
        # Dipanggil via command /ai
        if "last_file_key" not in context.user_data:
            await update.message.reply_text(
                "⚠️ Kirim video dulu sebelum menggunakan AI Prompt!"
            )
            return
        await update.message.reply_text(
            "🤖 *AI Prompt Aktif*\n\n"
            "Ketik instruksi video kamu dalam bahasa bebas:\n\n"
            "• _potong 30 detik pertama_\n"
            "• _ambil menit ke 1 sampai menit ke 2_\n"
            "• _kompres jadi lebih kecil_\n"
            "• _buat jadi format reels/shorts/tiktok_\n"
            "• _ekstrak audionya saja_\n\n"
            "Ketik /batal untuk membatalkan.",
            parse_mode="Markdown"
        )
        context.user_data["state"] = "waiting_ai_prompt"

async def ai_prompt_process(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Proses instruksi user dengan GPT-4o"""
    user_instruction = update.message.text
    
    # Ambil file_key
    file_key = context.user_data.get("ai_pending_file") or context.user_data.get("last_file_key")
    file_id = context.user_data.get(file_key) if file_key else None

    if not file_id:
        await update.message.reply_text("⚠️ Tidak ada video yang ditemukan. Kirim video dulu!")
        context.user_data["state"] = None
        return

    # Reset state
    context.user_data["state"] = None

    processing_msg = await update.message.reply_text(
        f"🤖 AI sedang menganalisis instruksi:\n_{user_instruction}_\n\n⏳ Mohon tunggu...",
        parse_mode="Markdown"
    )

    try:
        # Analisis instruksi dengan GPT-4o
        response = await client.chat.completions.create(
            model="gpt-4o",
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_instruction}
            ],
            max_tokens=300,
            temperature=0.3
        )

        raw = response.choices[0].message.content.strip()
        
        # Parse JSON
        try:
            result = json.loads(raw)
        except json.JSONDecodeError:
            # Coba extract JSON dari response
            import re
            json_match = re.search(r'\{.*\}', raw, re.DOTALL)
            if json_match:
                result = json.loads(json_match.group())
            else:
                raise ValueError("AI tidak menghasilkan JSON yang valid")

        action = result.get("action")
        params = result.get("params", {})
        explanation = result.get("explanation", "Memproses video...")

        await processing_msg.edit_text(
            f"🤖 AI memutuskan: *{explanation}*\n\n⏳ Memproses video...",
            parse_mode="Markdown"
        )

        # Download video
        file = await context.bot.get_file(file_id)
        input_path = os.path.join(DOWNLOAD_DIR, f"{file_key}_input.mp4")
        await file.download_to_drive(input_path)

        # Proses sesuai aksi
        output_path, success, send_type = await execute_action(action, params, input_path, file_key)

        if success and os.path.exists(output_path):
            await processing_msg.edit_text("✅ Selesai! Mengirim hasil...")
            with open(output_path, "rb") as f:
                caption = f"🤖 AI: {explanation}"
                if send_type == "audio":
                    await context.bot.send_audio(chat_id=update.effective_chat.id, audio=f, caption=caption)
                else:
                    await context.bot.send_video(chat_id=update.effective_chat.id, video=f, caption=caption)
            await processing_msg.delete()
        else:
            await processing_msg.edit_text("❌ Gagal memproses video. Coba instruksi yang lebih spesifik.")

    except Exception as e:
        logger.error(f"AI prompt error: {e}")
        await processing_msg.edit_text(f"❌ Error: {str(e)}")
    finally:
        # Cleanup
        for path in [input_path if 'input_path' in locals() else "", 
                     output_path if 'output_path' in locals() else ""]:
            if path and os.path.exists(path):
                os.remove(path)

async def execute_action(action: str, params: dict, input_path: str, file_key: str):
    """Eksekusi FFmpeg berdasarkan aksi dari AI"""
    send_type = "video"
    
    if action == "trim":
        output_path = os.path.join(DOWNLOAD_DIR, f"{file_key}_trimmed.mp4")
        start = params.get("start", "00:00:00")
        duration = str(params.get("duration", "30"))
        cmd = ["ffmpeg", "-i", input_path, "-ss", start, "-t", duration, "-c", "copy", "-y", output_path]

    elif action == "compress":
        output_path = os.path.join(DOWNLOAD_DIR, f"{file_key}_compressed.mp4")
        crf = str(params.get("crf", 28))
        cmd = ["ffmpeg", "-i", input_path, "-vcodec", "libx264", "-crf", crf, "-preset", "fast", "-y", output_path]

    elif action == "audio":
        output_path = os.path.join(DOWNLOAD_DIR, f"{file_key}_audio.mp3")
        cmd = ["ffmpeg", "-i", input_path, "-vn", "-acodec", "mp3", "-ab", "192k", "-y", output_path]
        send_type = "audio"

    elif action == "convert":
        output_path = os.path.join(DOWNLOAD_DIR, f"{file_key}_converted.mp4")
        cmd = ["ffmpeg", "-i", input_path, "-c:v", "libx264", "-c:a", "aac", "-y", output_path]

    elif action == "short916":
        output_path = os.path.join(DOWNLOAD_DIR, f"{file_key}_short916.mp4")
        fmt = params.get("format", "blur")
        if fmt == "fill":
            vf = "scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920"
        elif fmt == "blur":
            vf = "[0:v]scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,boxblur=20[bg];[0:v]scale=1080:1920:force_original_aspect_ratio=decrease[fg];[bg][fg]overlay=(W-w)/2:(H-h)/2"
        elif fmt == "black":
            vf = "scale=1080:1920:force_original_aspect_ratio=decrease,pad=1080:1920:(ow-iw)/2:(oh-ih)/2:black"
        else:
            vf = "scale=1080:1920:force_original_aspect_ratio=decrease,pad=1080:1920:(ow-iw)/2:(oh-ih)/2:white"
        cmd = ["ffmpeg", "-i", input_path, "-vf", vf, "-c:a", "copy", "-y", output_path]

    else:
        return None, False, "video"

    try:
        result = subprocess.run(cmd, capture_output=True, timeout=300)
        success = result.returncode == 0
        if not success:
            logger.error(f"FFmpeg error: {result.stderr.decode()}")
        return output_path, success, send_type
    except Exception as e:
        logger.error(f"Execute action error: {e}")
        return output_path, False, send_type
