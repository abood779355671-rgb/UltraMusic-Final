# ==============================================================================
# iquery.py - Inline Query Handler
# ==============================================================================
# This plugin handles inline mode queries (@botname <query>).
#
# Features:
# - Search YouTube videos in inline mode
# - Display up to 15 results
# - Show thumbnails, duration, views, channel info
# - Users can select a video to share in any chat
#
# Usage: @UltraMusicBot search query
# ==============================================================================

import asyncio
import logging

import yt_dlp
from ytlookup import videosearch
from pyrogram import types

from UltraMusic import app
from UltraMusic.helpers import buttons

logger = logging.getLogger(__name__)


def _ytdlp_search_15(text: str) -> list:
    """Blocking yt-dlp search — must be called via asyncio.to_thread."""
    opts = {
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "extract_flat": True,
        "socket_timeout": 20,
        "extractor_retries": 3,
        "extractor_args": {"youtube": {"player_client": ["android"]}},
    }
    with yt_dlp.YoutubeDL(opts) as ydl:
        result = ydl.extract_info(f"ytsearch15:{text}", download=False)
    return (result or {}).get("entries") or []


async def _search_with_fallback(text: str) -> list:
    """Try videosearch first; fall back to yt-dlp if it fails or returns nothing."""
    try:
        results_data = await asyncio.wait_for(
            videosearch(text, limit=15).next(),
            timeout=10,
        )
        results = results_data.get("result", [])
        if results:
            return results
    except Exception as e:
        logger.warning(f"videosearch failed for '{text}': {type(e).__name__}: {e}")

    # Fallback: yt-dlp runs blocking I/O on a separate thread
    logger.info(f"Falling back to yt-dlp for inline query: '{text}'")
    try:
        entries = await asyncio.to_thread(_ytdlp_search_15, text)
        # Normalise yt-dlp entry dicts to the shape the builder below expects
        results = []
        for e in entries:
            if not e:
                continue
            vid = e.get("id", "")
            results.append({
                "title": e.get("title", "Unknown Title"),
                "link": e.get("url") or (f"https://www.youtube.com/watch?v={vid}" if vid else ""),
                "duration": e.get("duration_string") or str(e.get("duration", "N/A")),
                "thumbnails": [{"url": e.get("thumbnail", "")}],
                "channel": {"name": e.get("channel") or e.get("uploader", "Unknown Channel"),
                            "link": e.get("channel_url") or e.get("uploader_url", "https://youtube.com")},
                "viewCount": {"short": str(e.get("view_count", "N/A"))},
                "publishedTime": e.get("upload_date", "N/A"),
            })
        return results
    except Exception as e:
        logger.warning(f"yt-dlp fallback also failed for '{text}': {type(e).__name__}: {e}")
        return []


@app.on_inline_query(~app.bl_users)
async def inline_query_handler(_, query: types.InlineQuery):
    text = query.query.strip().lower()
    if not text:
        return

    try:
        results = await _search_with_fallback(text)

        if not results:
            return

        answers = []
        for video in results:
            title = video.get("title", "Unknown Title").title()
            link = video.get("link", "https://youtube.com")
            duration = video.get("duration", "N/A")
            thumbnail = (
                video.get("thumbnails", [{}])[0].get("url", "").split("?")[0]
            )
            channel = video.get("channel", {}).get("name", "Unknown Channel")
            channellink = video.get("channel", {}).get(
                "link", "https://youtube.com"
            )
            views = video.get("viewCount", {}).get("short", "N/A")
            published = video.get("publishedTime", "N/A")

            description = f"{views} | {duration} | {channel} | {published}"
            caption = (
                f"<b>Title:</b> <a href='{link}'>{title[:250]}</a>\n\n"
                f"<b>Duration:</b> {duration}\n"
                f"<b>Views:</b> <code>{views}</code>\n"
                f"<b>Channel:</b> <a href='{channellink}'>{channel}</a>\n"
                f"<b>Published:</b> {published}\n\n"
                f"<u><i>Fetched by {app.name}</i></u>"
            )

            answers.append(
                types.InlineQueryResultPhoto(
                    photo_url=thumbnail,
                    title=title,
                    description=description,
                    caption=caption,
                    reply_markup=buttons.yt_key(link),
                )
            )

        if answers:
            await app.answer_inline_query(
                query.id, results=answers, cache_time=5
            )
    except Exception as e:
        logger.warning(f"Inline query error: {type(e).__name__}: {e}")
