import os
import json
import subprocess
import tempfile
import logging
import re
import uuid
from openai import AsyncOpenAI
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

logger = logging.getLogger(__name__)
client = AsyncOpenAI(api_key=os.getenv("OPENAI_API_KEY"))
DOWNLOAD_DIR = tempfile.gettempdir()

# ─────────────────────────────────────────────
# SYSTEM PROMPTS
# ─────────────────────────────────────────────

ANALYSIS_PROMPT = """Kamu adalah editor video profesional yang menganalisis konten video.

Berdasarkan informasi yang tersedia dan instruksi user, temukan momen-momen terbaik.

ATURAN PENTING:
- Jika hanya ada 1 momen relevan → kembalikan 1 item saja
- Jangan paksa jumlah tertentu — sesuaikan dengan konten
- Setiap clip minimal 10 detik, maksimal 3 menit
- Hindari overlap antar clip
- Jika konten berupa segmen tanpa dialog, pilih segmen yang relevan dengan instruksi

Format response (JSON array saja, tanpa teks lain):
[
  {
    "start": 15.5,
    "end": 45.0,
    "reason": "Alasan singkat mengapa momen ini dipilih",
    "label": "nama_clip_singkat"
  }
]

Jika tidak ada momen yang cocok → kembalikan array kosong []"""

TASK_PROMPT = """Kamu adalah asisten editor video.
Analisis instruksi user dan tentukan aksi tambahan apa yang perlu dilakukan pada setiap clip.

Format response (JSON array aksi tambahan, tanpa teks lain):
["short916_blur", "compress", "audio", "convert"]

Jika hanya minta clip/trim tanpa aksi lain → kembalikan []
Aksi yang tersedia: "short916_blur", "short916_fill", "short916_black", "compress", "audio", "convert"
"""

# ─────────────────────────────────────────────
# HELPER FUNCTIONS
# ─────────────────────────────────────────────

def run_ffmpeg(cmd, timeout=600):
    try:
        result = subprocess.run(cmd, capture_output=True, timeout=timeout)
        if result.returncode != 0:
            logger.error(f"FFmpeg error: {result.stderr.decode()[:500]}")
        return result.returncode == 0
    except Exception as e:
        logger.error(f"FFmpeg exception: {e}")
        return False

def extract_audio_for_whisper(input_path, output_path):
    cmd = ["ffmpeg", "-i", input_path, "-vn", "-acodec", "mp3",
           "-ab", "64k", "-ar", "16000", "-ac", "1", "-y", output_path]
    return run_ffmpeg(cmd, timeout=120)

def extract_thumbnail(input_path, output_path, timestamp):
    cmd = ["ffmpeg", "-ss", str(timestamp), "-i", input_path,
           "-vframes", "1", "-q:v", "2", "-y", output_path]
    return run_ffmpeg(cmd, timeout=30)

def trim_clip(input_path, output_path, start, end):
    duration = end - start
    cmd = ["ffmpeg", "-i", input_path, "-ss", str(start),
           "-t", str(duration), "-c", "copy", "-y", output_path]
    return run_ffmpeg(cmd)

def apply_action(input_path, output_path, action):
    if action == "short916_blur":
        vf = ("[0:v]scale=1080:1920:force_original_aspect_ratio=increase,"
              "crop=1080:1920,boxblur=20[bg];"
              "[0:v]scale=1080:1920:force_original_aspect_ratio=decrease[fg];"
              "[bg][fg]overlay=(W-w)/2:(H-h)/2")
        cmd = ["ffmpeg", "-i", input_path, "-filter_complex", vf, "-map", "0:a?", "-c:a", "copy", "-y", output_path]
    elif action == "short916_fill":
        vf = "scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920"
        cmd = ["ffmpeg", "-i", input_path, "-vf", vf, "-c:a", "copy", "-y", output_path]
    elif action == "short916_black":
        vf = "scale=1080:1920:force_original_aspect_ratio=decrease,pad=1080:1920:(ow-iw)/2:(oh-ih)/2:black"
        cmd = ["ffmpeg", "-i", input_path, "-vf", vf, "-c:a", "copy", "-y", output_path]
    elif action == "compress":
        cmd = ["ffmpeg", "-i", input_path, "-vcodec", "libx264", "-crf", "28", "-preset", "fast", "-y", output_path]
    elif action == "audio":
        output_path = output_path.replace(".mp4", ".mp3")
        cmd = ["ffmpeg", "-i", input_path, "-vn", "-acodec", "mp3", "-ab", "192k", "-y", output_path]
    elif action == "convert":
        cmd = ["ffmpeg", "-i", input_path, "-c:v", "libx264", "-c:a", "aac", "-y", output_path]
    else:
        return False, output_path, "video"

    send_type = "audio" if action == "audio" else "video"
    success = run_ffmpeg(cmd)
    return success, output_path, send_type

def get_video_duration(input_path):
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", input_path],
            capture_output=True, text=True, timeout=30
        )
        return float(result.stdout.strip())
    except Exception:
        return 0

def make_fallback_segments(duration):
    """Bagi video jadi segmen rata jika Whisper gagal"""
    segments = []
    interval = max(30, duration / min(5, max(1, int(duration / 30))))
    t = 0
    i = 1
    while t < duration:
        end = min(t + interval, duration)
        class Seg:
            pass
        s = Seg()
        s.start = t
        s.end = end
        s.text = f"[segmen {i}: {t:.0f}s hingga {end:.0f}s]"
        segments.append(s)
        t += interval
        i += 1
    return segments

# ─────────────────────────────────────────────
# MAIN HANDLERS
# ─────────────────────────────────────────────

async def generate_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["generate_state"] = "waiting_video"
    context.user_data["generate_file_id"] = None
    context.user_data["generate_is_local"] = False

    await update.message.reply_text(
        "🎬 *Generate Mode*\n\n"
        "Langkah 1️⃣ — Kirim video atau link YouTube:\n\n"
        "• Upload file video langsung (maks 50MB)\n"
        "• Atau kirim link YouTube\n\n"
        "Ketik /batal untuk keluar.",
        parse_mode="Markdown"
    )


async def generate_receive_video(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if context.user_data.get("generate_state") != "waiting_video":
        return False

    message = update.message
    video = message.video or message.document
    if not video:
        return False

    if video.file_size and video.file_size > 50 * 1024 * 1024:
        await message.reply_text("❌ File terlalu besar! Maks 50MB.")
        return True

    file_key = str(uuid.uuid4())[:8]
    context.user_data["generate_file_id"] = video.file_id
    context.user_data["generate_file_key"] = file_key
    context.user_data["generate_state"] = "waiting_prompt"

    await message.reply_text(
        "✅ *Video diterima!*\n\n"
        "Langkah 2️⃣ — Ketik instruksi dalam 1 pesan:\n\n"
        "Contoh:\n"
        "• _cari momen lucu_\n"
        "• _ambil bagian penting dan jadikan 9:16_\n"
        "• _jadikan clips edukasi_\n"
        "• _buat shorts dari bagian paling menarik_\n\n"
        "👇 Ketik instruksi kamu:",
        parse_mode="Markdown"
    )
    return True


async def generate_receive_link(update: Update, context: ContextTypes.DEFAULT_TYPE, url: str):
    if context.user_data.get("generate_state") != "waiting_video":
        return False

    processing = await update.message.reply_text("⏳ Mengunduh video YouTube...")
    file_key = str(uuid.uuid4())[:8]
    output_path = os.path.join(DOWNLOAD_DIR, f"{file_key}_yt.mp4")

    try:
        cmd = ["yt-dlp", "-f", "best[height<=720]", "-o", output_path, url]
        result = subprocess.run(cmd, capture_output=True, timeout=300)

        if result.returncode != 0 or not os.path.exists(output_path):
            await processing.edit_text("❌ Gagal download. Cek link dan coba lagi.")
            return True

        context.user_data["generate_local_path"] = output_path
        context.user_data["generate_file_key"] = file_key
        context.user_data["generate_is_local"] = True
        context.user_data["generate_state"] = "waiting_prompt"

        await processing.edit_text(
            "✅ *Video YouTube diunduh!*\n\n"
            "Langkah 2️⃣ — Ketik instruksi:\n\n"
            "• _cari momen lucu_\n"
            "• _jadikan clips edukasi_\n\n"
            "👇 Ketik instruksi kamu:",
            parse_mode="Markdown"
        )
    except Exception as e:
        await processing.edit_text(f"❌ Error: {str(e)}")

    return True


async def generate_process_prompt(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if context.user_data.get("generate_state") != "waiting_prompt":
        return False

    user_prompt = update.message.text
    file_key = context.user_data.get("generate_file_key")
    is_local = context.user_data.get("generate_is_local", False)

    context.user_data["generate_state"] = None

    processing = await update.message.reply_text(
        "⏳ *Langkah 1/4* — Mengunduh video...",
        parse_mode="Markdown"
    )

    input_path = None
    audio_path = None
    whisper_success = False

    try:
        # 1. Siapkan video
        if is_local:
            input_path = context.user_data.get("generate_local_path")
        else:
            file_id = context.user_data.get("generate_file_id")
            file = await context.bot.get_file(file_id)
            input_path = os.path.join(DOWNLOAD_DIR, f"{file_key}_input.mp4")
            await file.download_to_drive(input_path)

        duration = get_video_duration(input_path)
        if duration == 0:
            duration = 60  # fallback default

        # 2. Coba Whisper
        await processing.edit_text("⏳ *Langkah 2/4* — Transkripsi audio...", parse_mode="Markdown")
        audio_path = os.path.join(DOWNLOAD_DIR, f"{file_key}_audio.mp3")
        
        transcript_text = ""
        segments = []

        audio_ok = extract_audio_for_whisper(input_path, audio_path)

        if audio_ok and os.path.exists(audio_path) and os.path.getsize(audio_path) > 1000:
            try:
                with open(audio_path, "rb") as af:
                    transcript = await client.audio.transcriptions.create(
                        model="whisper-1",
                        file=af,
                        response_format="verbose_json",
                        timestamp_granularities=["segment"]
                    )
                segments = transcript.segments or []
                transcript_text = transcript.text or ""
                whisper_success = bool(transcript_text.strip())
            except Exception as e:
                logger.warning(f"Whisper gagal: {e}")
                whisper_success = False

        # Fallback jika Whisper gagal
        if not whisper_success:
            await processing.edit_text(
                "⚠️ *Audio tidak terdeteksi* — menggunakan scene detection...\n\n"
                "⏳ *Langkah 2/4* — Analisis durasi video...",
                parse_mode="Markdown"
            )
            segments = make_fallback_segments(duration)
            transcript_text = " ".join([s.text for s in segments])

        # 3. GPT analisis
        await processing.edit_text("⏳ *Langkah 3/4* — AI menganalisis konten...", parse_mode="Markdown")

        # Buat transcript dengan timestamp
        if whisper_success:
            transcript_with_ts = ""
            for seg in segments[:100]:
                transcript_with_ts += f"[{seg.start:.1f}s - {seg.end:.1f}s] {seg.text}\n"
        else:
            transcript_with_ts = "\n".join([
                f"[{s.start:.0f}s - {s.end:.0f}s] {s.text}" for s in segments
            ])

        # Analisis momen
        moments_response = await client.chat.completions.create(
            model="gpt-4o",
            messages=[
                {"role": "system", "content": ANALYSIS_PROMPT},
                {"role": "user", "content": (
                    f"Instruksi: {user_prompt}\n"
                    f"Durasi video: {duration:.0f} detik\n"
                    f"{'Transkrip:' if whisper_success else 'Segmen video (tanpa dialog):'}\n"
                    f"{transcript_with_ts}"
                )}
            ],
            max_tokens=800,
            temperature=0.3
        )

        # Analisis aksi tambahan
        tasks_response = await client.chat.completions.create(
            model="gpt-4o",
            messages=[
                {"role": "system", "content": TASK_PROMPT},
                {"role": "user", "content": user_prompt}
            ],
            max_tokens=100,
            temperature=0.2
        )

        # Parse moments
        moments_raw = moments_response.choices[0].message.content.strip()
        try:
            json_match = re.search(r'\[.*\]', moments_raw, re.DOTALL)
            moments = json.loads(json_match.group()) if json_match else []
        except Exception:
            moments = []

        # Parse extra tasks
        tasks_raw = tasks_response.choices[0].message.content.strip()
        try:
            json_match = re.search(r'\[.*\]', tasks_raw, re.DOTALL)
            extra_tasks = json.loads(json_match.group()) if json_match else []
        except Exception:
            extra_tasks = []

        # Jika tidak ada momen, buat 1 clip dari keseluruhan video
        if not moments:
            moments = [{
                "start": 0,
                "end": min(duration, 60),
                "reason": "Tidak ada momen spesifik, mengambil bagian awal video",
                "label": "clip_utama"
            }]

        # 4. Generate thumbnails & preview
        await processing.edit_text(
            f"⏳ *Langkah 4/4* — Membuat preview {len(moments)} momen...",
            parse_mode="Markdown"
        )

        # Simpan data untuk konfirmasi
        context.user_data["pending_moments"] = moments
        context.user_data["pending_input_path"] = input_path
        context.user_data["pending_extra_tasks"] = extra_tasks
        context.user_data["pending_file_key"] = file_key

        await processing.delete()

        # Summary
        mode_info = "🎙️ Mode: Transkripsi dialog" if whisper_success else "🎬 Mode: Scene detection (video tanpa dialog)"
        summary = f"{mode_info}\n\n🤖 AI menemukan *{len(moments)} momen*:\n\n"
        for i, m in enumerate(moments):
            start = m.get("start", 0)
            end = m.get("end", 0)
            reason = m.get("reason", "")
            label = m.get("label", f"clip_{i+1}")
            summary += f"*{i+1}. {label}*\n"
            summary += f"   ⏱ {start:.0f}s — {end:.0f}s ({end-start:.0f} detik)\n"
            summary += f"   💡 {reason}\n\n"

        if extra_tasks:
            summary += f"🎨 Aksi tambahan: {', '.join(extra_tasks)}\n\n"

        summary += "📸 Mengirim thumbnail preview..."
        await update.message.reply_text(summary, parse_mode="Markdown")

        # Kirim thumbnail
        for i, moment in enumerate(moments):
            start = moment.get("start", 0)
            end = moment.get("end", 0)
            mid = (start + end) / 2
            label = moment.get("label", f"clip_{i+1}")

            thumb_path = os.path.join(DOWNLOAD_DIR, f"{file_key}_thumb_{i}.jpg")
            if extract_thumbnail(input_path, thumb_path, mid):
                with open(thumb_path, "rb") as tf:
                    await context.bot.send_photo(
                        chat_id=update.effective_chat.id,
                        photo=tf,
                        caption=f"📸 Preview *{i+1}. {label}*\n⏱ {start:.0f}s — {end:.0f}s",
                        parse_mode="Markdown"
                    )
                os.remove(thumb_path)

        # Tombol konfirmasi
        keyboard = [
            [InlineKeyboardButton("✅ Proses Semua Clip", callback_data=f"gen_confirm|{file_key}")],
            [InlineKeyboardButton("❌ Batalkan", callback_data=f"gen_cancel|{file_key}")]
        ]

        await update.message.reply_text(
            "👆 Preview momen yang akan diproses.\n\nLanjutkan?",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )

    except Exception as e:
        logger.error(f"Generate error: {e}")
        await processing.edit_text(f"❌ Error: {str(e)}")

    finally:
        if audio_path and os.path.exists(audio_path):
            os.remove(audio_path)

    return True


async def generate_confirm_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    data = query.data.split("|")
    action = data[0]
    file_key = data[1] if len(data) > 1 else ""

    if action == "gen_cancel":
        await query.edit_message_text("❌ Dibatalkan.")
        return

    moments = context.user_data.get("pending_moments", [])
    input_path = context.user_data.get("pending_input_path")
    extra_tasks = context.user_data.get("pending_extra_tasks", [])

    if not moments or not input_path:
        await query.edit_message_text("❌ Data tidak ditemukan. Mulai ulang dengan /generate")
        return

    await query.edit_message_text(f"⚙️ Memproses {len(moments)} clip...")

    results = []

    for i, moment in enumerate(moments):
        start = moment.get("start", 0)
        end = moment.get("end", 0)
        label = moment.get("label", f"clip_{i+1}")

        await query.edit_message_text(
            f"⚙️ Memproses clip {i+1}/{len(moments)}: *{label}*...",
            parse_mode="Markdown"
        )

        clip_path = os.path.join(DOWNLOAD_DIR, f"{file_key}_clip_{i}.mp4")
        if not trim_clip(input_path, clip_path, start, end):
            continue

        results.append((clip_path, "video", label))

        for task in extra_tasks:
            ext = ".mp3" if task == "audio" else ".mp4"
            task_path = os.path.join(DOWNLOAD_DIR, f"{file_key}_clip_{i}_{task}{ext}")
            success, out_path, send_type = apply_action(clip_path, task_path, task)
            if success and os.path.exists(out_path):
                results.append((out_path, send_type, f"{label}_{task}"))

    await query.edit_message_text(f"📤 Mengirim {len(results)} file...")

    for path, send_type, label in results:
        try:
            with open(path, "rb") as f:
                caption = f"🎬 {label}"
                if send_type == "audio":
                    await context.bot.send_audio(chat_id=query.message.chat_id, audio=f, caption=caption)
                else:
                    await context.bot.send_video(chat_id=query.message.chat_id, video=f, caption=caption)
            os.remove(path)
        except Exception as e:
            logger.error(f"Send error: {e}")

    await query.edit_message_text(
        f"✅ *Selesai!* {len(results)} file berhasil dikirim.\n\nMau proses video lain? /generate",
        parse_mode="Markdown"
    )

    if input_path and os.path.exists(input_path):
        os.remove(input_path)
