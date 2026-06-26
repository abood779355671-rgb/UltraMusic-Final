# ==============================================================================
# cleanmode.py - Clean Mode Plugin
# ==============================================================================
# Command:
#   /cleanmode on  → Enable clean mode (auto-delete Now Playing msg after track ends)
#   /cleanmode off → Disable clean mode
#
# When clean mode is ON the bot deletes the "Now Playing" message automatically
# once the current track finishes.  The calls.py layer should call
# `handle_cleanmode_delete(chat_id, message_id)` after each track ends.
# ==============================================================================

import asyncio

from pyrogram import filters
from pyrogram.types import Message

from UltraMusic import app, db

# In-memory store: chat_id → message_id to delete when track ends
_pending_delete: dict[int, int] = {}


async def register_now_playing(chat_id: int, message_id: int) -> None:
    """
    Call this right after sending the 'Now Playing' message.
    If clean mode is ON the message_id is stored so it can be deleted later.
    """
    if await db.is_cleanmode_on(chat_id):
        _pending_delete[chat_id] = message_id


async def handle_cleanmode_delete(chat_id: int) -> None:
    """
    Call this when a track ends (from calls.py or the stream-end handler).
    Deletes the stored Now Playing message if clean mode is ON.
    """
    if not await db.is_cleanmode_on(chat_id):
        return
    msg_id = _pending_delete.pop(chat_id, None)
    if msg_id:
        try:
            await app.delete_messages(chat_id, msg_id)
        except Exception:
            pass  # Message may already be deleted; ignore silently


# ── /cleanmode command ────────────────────────────────────────────────────────

@app.on_message(filters.command("cleanmode") & filters.group)
async def cleanmode_cmd(_, message: Message):
    """Enable or disable clean mode for the chat."""
    admins = await db.get_admins(message.chat.id)
    if message.from_user.id not in admins:
        return await message.reply(
            "<blockquote>❌ هذا الأمر للمشرفين فقط.</blockquote>",
            parse_mode="html",
        )

    args = message.text.split()
    if len(args) < 2 or args[1].lower() not in ("on", "off"):
        current = await db.is_cleanmode_on(message.chat.id)
        status = "✅ مفعّل" if current else "❌ معطّل"
        return await message.reply(
            f"<blockquote><b>🧹 الوضع النظيف (Clean Mode)</b>\n\n"
            f"الحالة الحالية: {status}\n\n"
            f"الاستخدام:\n"
            f"/cleanmode on — تفعيل\n"
            f"/cleanmode off — تعطيل</blockquote>",
            parse_mode="html",
        )

    action = args[1].lower()
    if action == "on":
        await db.cleanmode_on(message.chat.id)
        await message.reply(
            "<blockquote>✅ <b>تم تفعيل الوضع النظيف.</b>\n\n"
            "سيتم حذف رسالة «Now Playing» تلقائياً بعد انتهاء كل أغنية.</blockquote>",
            parse_mode="html",
        )
    else:
        await db.cleanmode_off(message.chat.id)
        await message.reply(
            "<blockquote>❌ <b>تم تعطيل الوضع النظيف.</b>\n\n"
            "لن يتم حذف رسائل «Now Playing» بعد الآن.</blockquote>",
            parse_mode="html",
        )
