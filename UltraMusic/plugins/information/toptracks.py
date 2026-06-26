# ==============================================================================
# toptracks.py - Top Tracks Statistics
# ==============================================================================
# Commands:
#   /topchat   → Top 10 most-played tracks in this group
#   /topglobal → Top 10 most-played tracks across all groups
#   /topuser   → Top 10 most-played tracks by the sender
#
# Track data is recorded via db.increment_track() called from the play handler.
# ==============================================================================

from pyrogram import filters
from pyrogram.types import Message

from UltraMusic import app, db, lang


def _format_list(title: str, tracks: dict) -> str:
    """Format a top-tracks dict into a readable message."""
    if not tracks:
        return f"<blockquote><b>{title}</b>\n\nلا توجد بيانات بعد. شغّل بعض الأغاني أولاً! 🎵</blockquote>"

    lines = [f"<blockquote><b>{title}</b>\n"]
    for rank, (vidid, data) in enumerate(tracks.items(), start=1):
        count = data["count"] if isinstance(data, dict) else data
        track_title = (data.get("title", "") if isinstance(data, dict) else "") or ""
        url = f"https://youtu.be/{vidid}"
        medal = {1: "🥇", 2: "🥈", 3: "🥉"}.get(rank, f"<b>{rank}.</b>")
        label = track_title if track_title else vidid
        lines.append(f"{medal} <a href='{url}'>{label}</a> — <code>{count}</code> مرة")

    lines.append("</blockquote>")
    return "\n".join(lines)


# ── /topglobal ────────────────────────────────────────────────────────────────

@app.on_message(filters.command(["أكثر_عالمي", "topglobal", "gtop"]) & filters.group & ~app.bl_users)
@lang.language()
async def top_global_cmd(_, message: Message):
    """Show top 10 globally played tracks."""
    try:
        await message.delete()
    except Exception:
        pass
    msg = await message.reply(
        f"<blockquote>{message.lang['top_global_loading']}</blockquote>",
        parse_mode="html",
    )
    tracks = await db.get_global_tops()
    text = _format_list(message.lang["top_global_title"], tracks)
    await msg.edit_text(text, parse_mode="html", disable_web_page_preview=True)


# ── /topchat ──────────────────────────────────────────────────────────────────

@app.on_message(filters.command(["أكثر_مجموعة", "topchat", "ctop"]) & filters.group & ~app.bl_users)
@lang.language()
async def top_chat_cmd(_, message: Message):
    """Show top 10 tracks in this group."""
    try:
        await message.delete()
    except Exception:
        pass
    msg = await message.reply(
        f"<blockquote>{message.lang['top_chat_loading']}</blockquote>",
        parse_mode="html",
    )
    tracks = await db.get_chat_tops(message.chat.id)
    text = _format_list(message.lang["top_chat_title"], tracks)
    await msg.edit_text(text, parse_mode="html", disable_web_page_preview=True)


# ── /topuser ──────────────────────────────────────────────────────────────────

@app.on_message(filters.command(["أكثر_مستخدم", "topuser", "utop"]) & filters.group & ~app.bl_users)
@lang.language()
async def top_user_cmd(_, message: Message):
    """Show top 10 tracks for the requesting user."""
    try:
        await message.delete()
    except Exception:
        pass
    msg = await message.reply(
        f"<blockquote>{message.lang['top_user_loading']}</blockquote>",
        parse_mode="html",
    )
    tracks = await db.get_user_tops(message.from_user.id)
    name = message.from_user.first_name or "المستخدم"
    text = _format_list(message.lang["top_user_title_prefix"] + name, tracks)
    await msg.edit_text(text, parse_mode="html", disable_web_page_preview=True)
