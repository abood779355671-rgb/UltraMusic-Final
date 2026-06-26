# ==============================================================================
# song.py - Search & Download Command (بحث)
# ==============================================================================
# Downloads a track from YouTube and sends it directly as an audio file in the
# chat. This is completely independent from the voice-chat playback system
# (/تشغيل) - it never joins the voice chat, it just searches, downloads, and
# uploads the file.
#
# Command:
#   بحث <اسم الأغنية>                → بحث نصي، يأخذ أول نتيجة
#   بحث <رابط يوتيوب>                → تحميل مباشر من الرابط
#   ردّ على رسالة فيها رابط + بحث    → تحميل الرابط المردود عليه
#
# Notes:
# - يعيد استخدام yt.url() / yt.valid() / yt.search() / yt.download() الموجودة
#   فعلياً في core/youtube.py (نفس المنطق المستخدم في /تشغيل بالضبط).
# - يتم تحويل الملف دائماً إلى MP3 حقيقي عبر ffmpeg (راجع _ensure_mp3) بدل
#   إرسال صيغة التحميل الخام مباشرة - هذا يضمن ظهوره كملف صوتي صحيح في
#   تيليجرام حتى لو كان الكاش المخزّن أصلاً mp4 (من تشغيل فيديو سابق).
# - يحترم نفس كاش التحميل في downloads/ المستخدم من قبل /تشغيل، ويضيف نسخة
#   mp3 مخزّنة بشكل منفصل لإعادة استخدامها لاحقاً بسرعة.
# ==============================================================================

import os
import asyncio
import logging

from pyrogram import filters, enums
from pyrogram.types import Message
from PIL import Image

from UltraMusic import app, config, db, lang, yt
from UltraMusic.helpers import command, thumb

logger = logging.getLogger(__name__)


async def _ensure_mp3(src_path: str, video_id: str) -> str | None:
    """
    Convert/ensure the file sent by /بحث is always a proper standalone MP3.

    yt.download() may return a cached .m4a/.opus file, OR (if this video was
    previously played with /فيديو in this chat) a cached .mp4 left over from
    video playback. Telegram detects the .mp4 container as video, so even
    though we send it via reply_audio() it shows up as a generic file/video
    attachment instead of a clean audio player bubble.

    Re-encoding to .mp3 here guarantees a correct audio/mpeg file every time,
    regardless of what container was on disk. ffmpeg is already a dependency
    of this project (used by core/youtube.py for video downloads), so no new
    dependency is introduced. The result is cached as downloads/{id}.mp3, so
    repeated /بحث (or /تشغيل) calls for the same track reuse it instantly.
    """
    mp3_path = f"downloads/{video_id}.mp3"
    if os.path.isfile(mp3_path):
        return mp3_path

    try:
        proc = await asyncio.create_subprocess_exec(
            "ffmpeg", "-y", "-i", src_path,
            "-vn", "-acodec", "libmp3lame", "-b:a", "128k",
            mp3_path,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        await proc.wait()
    except Exception as e:
        logger.error(f"❌ ffmpeg mp3 conversion failed for {video_id}: {e}", exc_info=True)
        return None

    if proc.returncode == 0 and os.path.isfile(mp3_path):
        return mp3_path

    logger.error(f"❌ ffmpeg exited {proc.returncode} converting {video_id} to mp3")
    return None


def _prepare_thumb(path: str) -> str | None:
    """
    Resize a raw downloaded thumbnail to fit Telegram's strict thumb limits
    (max 320x320, JPEG, well under 200KB). Returns the path to the resized
    file, or None on failure.

    YouTube thumbnails (maxresdefault/hqdefault) are usually much bigger than
    this, which makes Telegram reject the whole reply_audio() call with a
    PHOTO_INVALID_DIMENSIONS-style error - this was the actual cause of
    "فشل إرسال الملف الصوتي".
    """
    try:
        with Image.open(path) as img:
            img = img.convert("RGB")
            img.thumbnail((320, 320))
            out_path = path.rsplit(".", 1)[0] + "_thumb.jpg"
            img.save(out_path, "JPEG", quality=85, optimize=True)
        return out_path
    except Exception as e:
        logger.warning(f"⚠️ Failed to resize song thumbnail: {e}")
        return None


@app.on_message(command(["بحث"]) & filters.group & ~app.bl_users)
@lang.language()
async def search_song(_, m: Message):
    # Auto-delete the command message itself (consistent with /تشغيل)
    try:
        await m.delete()
    except Exception:
        pass

    # ── Resolve the query: a link (typed or replied-to) or plain search text ──
    url = yt.url(m)
    if url and not yt.valid(url):
        return await m.reply_text(m.lang["song_unsupported"])

    if url:
        query = url
    elif len(m.command) > 1:
        query = m.text.split(None, 1)[1]
    else:
        return await m.reply_text(m.lang["song_usage"])

    status = await m.reply_text(m.lang["song_searching"])

    # ── Search (cached 10 min, same cache used by /تشغيل) ──
    track = await yt.search(query, m.id)
    if not track:
        return await status.edit_text(m.lang["song_not_found"])

    if track.is_live:
        return await status.edit_text(m.lang["song_live_unsupported"])

    try:
        song_download_limit = await db.get_setting("SONG_DOWNLOAD_LIMIT", config.SONG_DOWNLOAD_LIMIT)
    except Exception as _e:
        logger.warning(f"[settings] تعذّر قراءة SONG_DOWNLOAD_LIMIT من DB، سيُستخدم الافتراضي ({config.SONG_DOWNLOAD_LIMIT}): {_e}")
        song_download_limit = config.SONG_DOWNLOAD_LIMIT
    if track.duration_sec and track.duration_sec > song_download_limit:
        return await status.edit_text(
            m.lang["song_too_long"].format(song_download_limit // 60)
        )

    await status.edit_text(m.lang["song_downloading"].format(track.title))

    # ── Download (audio only, reuses the existing downloads/ cache) ──
    try:
        file_path = await yt.download(track.id)
    except Exception as e:
        logger.error(f"❌ yt.download failed for {track.id}: {e}", exc_info=True)
        file_path = None

    if not file_path or not os.path.isfile(file_path):
        return await status.edit_text(m.lang["song_download_failed"])

    # ── Guarantee a real MP3 file regardless of the cached source format ──
    mp3_path = await _ensure_mp3(file_path, track.id)
    if not mp3_path:
        return await status.edit_text(m.lang["song_download_failed"])
    file_path = mp3_path

    # ── Thumbnail (best effort - send without one if it fails) ──
    thumb_path = None
    if track.thumbnail:
        try:
            raw_thumb = await thumb.save_thumb(
                f"cache/song_{track.id}.jpg", track.thumbnail
            )
            thumb_path = _prepare_thumb(raw_thumb)
            if raw_thumb and os.path.isfile(raw_thumb) and raw_thumb != thumb_path:
                try:
                    os.remove(raw_thumb)
                except OSError:
                    pass
        except Exception as e:
            logger.warning(f"⚠️ Thumbnail download failed for {track.id}: {e}")
            thumb_path = None

    await status.edit_text(m.lang["song_uploading"])

    # ── Send the audio file directly in the chat ──
    async def _send(with_thumb: bool):
        await app.send_chat_action(chat_id=m.chat.id, action=enums.ChatAction.UPLOAD_AUDIO)
        await m.reply_audio(
            file_path,
            title=track.title,
            performer=track.channel_name or "YouTube",
            duration=track.duration_sec or 0,
            thumb=thumb_path if with_thumb else None,
            caption=m.lang["song_caption"].format(track.title),
        )

    try:
        try:
            await _send(with_thumb=True)
        except Exception as e:
            if thumb_path:
                # Retry once without the thumbnail instead of failing outright -
                # covers any remaining edge case Telegram rejects.
                logger.warning(
                    f"⚠️ reply_audio with thumb failed for {track.id}, retrying without thumb: {e}"
                )
                await _send(with_thumb=False)
            else:
                raise
    except Exception as e:
        logger.error(f"❌ Failed to send song {track.id}: {e}", exc_info=True)
        return await status.edit_text(m.lang["song_send_failed"])
    finally:
        # Clean up the temporary thumbnail (the audio file itself stays
        # cached in downloads/ so /تشغيل or /بحث can reuse it instantly).
        if thumb_path and os.path.isfile(thumb_path):
            try:
                os.remove(thumb_path)
            except OSError:
                pass

    try:
        await status.delete()
    except Exception:
        pass
