# ==============================================================================
# pause.py - Pause Playback Command
# ==============================================================================
# This plugin handles pausing the current voice chat playback.
#
# Commands:
# - /pause - Pause current playback
#
# Requirements:
# - User must be admin or authorized user
# - Music must be currently playing
# ==============================================================================

import logging
from pyrogram import filters, types
from pyrogram.errors import ChatSendPlainForbidden, ChatWriteForbidden

from UltraMusic import tune, app, db, lang
from UltraMusic.helpers import buttons, can_manage_vc, command

logger = logging.getLogger(__name__)


@app.on_message(command(["تجميد"]) & filters.group & ~app.bl_users)
@lang.language()
@can_manage_vc
async def _pause(_, m: types.Message):
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

    if not await db.playing(m.chat.id):
        try:
            return await m.reply_text(m.lang["play_already_paused"])
        except (ChatSendPlainForbidden, ChatWriteForbidden):
            return

    await tune.pause(m.chat.id)
    try:
        await m.reply_text(
            text=m.lang["play_paused"].format(m.from_user.mention),
            reply_markup=await buttons.controls(m.chat.id),
        )
    except (ChatSendPlainForbidden, ChatWriteForbidden):
        logger.warning("Cannot send text in media-only chat")
