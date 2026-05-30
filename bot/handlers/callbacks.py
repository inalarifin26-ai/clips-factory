import os
import subprocess
import tempfile
from telegram import Update
from telegram.ext import ContextTypes

DOWNLOAD_DIR = tempfile.gettempdir()

async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    data = query.data.split("|")
    action = data[0]
    file_id = data[1]
    file_type = data[2]

    await query.edit_message_text(f"⏳ Memproses... harap tunggu.")

    try:
        # Download file dari Telegram
        file = await context.bot.get_file(file_id)
        ext = "mp4" if file_type == "video" else "mp3"
        input_path = os.path.join(DOWNLOAD_DIR, f"{file_id}_input.{ext}")
        output_path = os.path.join(DOWNLOAD_DIR, f"{file_id}_output.{ext}")

        await file.download_to_drive(input_path)

        success = False

        if action == "compress":
            success = compress_video(input_path, output_path)
            caption = "🗜️ Video berhasil dikompres!"

        elif action == "audio":
            output_path = output_path.replace(".mp4", ".mp3")
            success = extract_audio(input_path, output_path)
            caption = "🎵 Audio berhasil diekstrak!"

        elif action == "convert":
            success = convert_to_mp4(input_path, output_path)
            caption = "🔄 Video berhasil diconvert ke MP4!"

        elif action == "trim":
            # Default trim: 0 - 30 detik
            success = trim_video(input_path, output_path, start="00:00:00", duration="30")
            caption = "✂️ Video berhasil dipotong (30 detik pertama)!"

        elif action == "to_mp3":
            output_path = output_path.replace(".mp3", "_converted.mp3")
            success = extract_audio(input_path, output_path)
            caption = "🔄 Berhasil diconvert ke MP3!"

        else:
            await query.edit_message_text("❌ Aksi tidak dikenali.")
            return

        if success and os.path.exists(output_path):
            await query.edit_message_text("✅ Selesai! Mengirim file...")
            with open(output_path, "rb") as f:
                if output_path.endswith(".mp3"):
                    await context.bot.send_audio(
                        chat_id=query.message.chat_id,
                        audio=f,
                        caption=caption
                    )
                else:
                    await context.bot.send_video(
                        chat_id=query.message.chat_id,
                        video=f,
                        caption=caption
                    )
            await query.delete_message()
        else:
            await query.edit_message_text("❌ Gagal memproses file. Coba lagi.")

    except Exception as e:
        await query.edit_message_text(f"❌ Error: {str(e)}")
    finally:
        # Cleanup
        for path in [input_path, output_path]:
            if os.path.exists(path):
                os.remove(path)


def run_ffmpeg(cmd: list) -> bool:
    try:
        result = subprocess.run(cmd, capture_output=True, timeout=300)
        return result.returncode == 0
    except Exception:
        return False

def compress_video(input_path: str, output_path: str) -> bool:
    cmd = [
        "ffmpeg", "-i", input_path,
        "-vcodec", "libx264", "-crf", "28",
        "-preset", "fast",
        "-y", output_path
    ]
    return run_ffmpeg(cmd)

def extract_audio(input_path: str, output_path: str) -> bool:
    cmd = [
        "ffmpeg", "-i", input_path,
        "-vn", "-acodec", "mp3",
        "-ab", "192k",
        "-y", output_path
    ]
    return run_ffmpeg(cmd)

def convert_to_mp4(input_path: str, output_path: str) -> bool:
    cmd = [
        "ffmpeg", "-i", input_path,
        "-c:v", "libx264", "-c:a", "aac",
        "-y", output_path
    ]
    return run_ffmpeg(cmd)

def trim_video(input_path: str, output_path: str, start: str, duration: str) -> bool:
    cmd = [
        "ffmpeg", "-i", input_path,
        "-ss", start,
        "-t", duration,
        "-c", "copy",
        "-y", output_path
    ]
    return run_ffmpeg(cmd)
