# ==============================================================================
# mute.py - Mute Voice Chat Command
# ==============================================================================
# This plugin mutes the assistant's audio stream in the current voice chat,
# without pausing or stopping playback - the track keeps playing internally,
# listeners just won't hear any audio until /unmute is used.
#
# Commands:
# - /mute - Mute the voice chat stream
#
# Requirements:
# - User must be a group admin (or the bot owner / sudo user)
# - A voice chat stream must currently be active in the group
# ==============================================================================

import logging
from pyrogram import filters, types
from pyrogram.errors import ChatSendPlainForbidden, ChatWriteForbidden

from UltraMusic import tune, app, db, lang
from UltraMusic.helpers import admin_check, command

logger = logging.getLogger(__name__)


@app.on_message(command(["كتم"]) & filters.group & ~app.bl_users)
@lang.language()
@admin_check
async def _mute(_, m: types.Message):
    # Auto-delete command message
    try:
        await m.delete()
    except Exception:
        pass

    if not await db.get_call(m.chat.id):
        try:
            return await m.reply_text(m.lang["not_playing"])
        except (ChatSendPlainForbidden, ChatWriteForbidden):
            return

    if await db.muted(m.chat.id):
        try:
            return await m.reply_text(
                "<blockquote>⚠️ البث مكتوم بالفعل.</blockquote>"
            )
        except (ChatSendPlainForbidden, ChatWriteForbidden):
            return

    success = await tune.mute(m.chat.id)
    if not success:
        try:
            return await m.reply_text(m.lang["not_playing"])
        except (ChatSendPlainForbidden, ChatWriteForbidden):
            return

    try:
        await m.reply_text(
            f"<blockquote><b>🔇 تم كتم البث بواسطة</b> {m.from_user.mention}</blockquote>"
        )
    except (ChatSendPlainForbidden, ChatWriteForbidden):
        logger.warning("Cannot send text in media-only chat")
