# ==============================================================================
# leave.py - Force Leave Commands (Sudo Only)
# ==============================================================================
# This plugin allows sudo users to make the bot and assistants leave chats.
#
# Commands:
# - /leave - Make bot and assistant leave the current chat
# - /leaveall - Make all assistants leave all inactive chats
#
# Only sudo users can use these commands.
# ==============================================================================

import asyncio
from pyrogram import filters, types, errors, enums

from UltraMusic import app, db, lang, logger, userbot, config


# ------------------------------------------------------------------------------
# دوال مشتركة — يستخدمها /leave و /leaveall ولوحة التحكم (panel:leave)
# لا تُكرَّر هذه المنطق في أي مكان آخر؛ كل المستهلكين يستدعون هاتين الدالتين.
# ------------------------------------------------------------------------------

async def _do_leave_chat(chat_id: int) -> tuple[bool, str | None]:
    """
    تنفّذ مغادرة البوت والمساعد (إن وُجد) من chat_id مُعطى.
    تُعيد (success, error_message). error_message=None عند النجاح.
    مُشتركة بين /leave (للمحادثة الحالية) وزر لوحة التحكم (لأي chat_id).
    """
    # حاول إخراج المساعد أولاً إن كان داخل المجموعة
    try:
        client = await db.get_client(chat_id)
        try:
            await client.leave_chat(chat_id)
        except errors.UserNotParticipant:
            # المساعد ليس داخل المجموعة، تجاهل
            pass
        except Exception as e:
            logger.debug(f"[leave] تعذّر إخراج المساعد من {chat_id}: {e}")
    except Exception:
        # فشل الحصول على المساعد — تابع لإخراج البوت بأي حال
        pass

    # أخرج البوت نفسه
    try:
        await app.leave_chat(chat_id)
    except Exception as e:
        return False, str(e)

    return True, None


async def _do_leave_all_idle() -> int:
    """
    تجعل كل المساعدين يغادرون كل المجموعات الخاملة (غير مشتركة في مكالمة نشطة)،
    باستثناء قنوات اللوق والمجموعات المستثناة.
    تُعيد العدد الإجمالي للمجموعات التي تمت مغادرتها.
    مُشتركة بين /leaveall وزر لوحة التحكم (panel:leave:idle).
    """
    total_left = 0

    for ub in userbot.clients:
        try:
            async for dialog in ub.get_dialogs():
                chat_id = dialog.chat.id

                # Skip logger and excluded chats
                excluded = [app.logger] + config.EXCLUDED_CHATS
                if chat_id in excluded:
                    continue

                # Only leave groups and supergroups
                if dialog.chat.type in [enums.ChatType.GROUP, enums.ChatType.SUPERGROUP]:
                    # Skip if currently in an active call
                    if chat_id in db.active_calls:
                        continue

                    try:
                        await ub.leave_chat(chat_id)
                        total_left += 1
                        await asyncio.sleep(1)  # Rate limit
                    except Exception as e:
                        logger.debug(f"Failed to leave {chat_id}: {e}")
                        continue

        except Exception as e:
            logger.error(f"Error in leave-all for assistant {ub.me.username if hasattr(ub, 'me') and ub.me else 'Unknown'}: {e}")
            continue

    return total_left


@app.on_message(filters.command(["leave"]) & app.sudo_filter)
@lang.language()
async def _leave(_, m: types.Message):
    """
    Command handler for /leave
    Makes both bot and assistant leave the current chat.
    """
    # Auto-delete command message
    try:
        await m.delete()
    except Exception:
        pass
    
    chat_id = m.chat.id
    chat_name = m.chat.title or "this chat"

    # Send confirmation message
    sent = await m.reply_text(
        f"<blockquote><b>👋 Leaving Chat</b></blockquote>\n\n"
        f"<blockquote>Bot and assistant are leaving <b>{chat_name}</b>...</blockquote>"
    )

    success, err = await _do_leave_chat(chat_id)
    if not success:
        # If bot can't leave, inform the sudo user
        await sent.edit_text(
            f"<blockquote><b>❌ Error</b></blockquote>\n\n"
            f"<blockquote>Failed to leave chat: {err}</blockquote>"
        )


@app.on_message(filters.command(["leaveall"]) & app.sudo_filter)
@lang.language()
async def _leaveall(_, m: types.Message):
    """
    Command handler for /leaveall
    Makes all assistants leave all inactive groups (not in active calls).
    """
    # Auto-delete command message
    try:
        await m.delete()
    except Exception:
        pass
    
    sent = await m.reply_text(
        f"<blockquote><b>🔄 Processing...</b></blockquote>\n\n"
        f"<blockquote>Making assistants leave all inactive groups...</blockquote>"
    )

    total_left = await _do_leave_all_idle()

    await sent.edit_text(
        f"<blockquote><b>✅ Cleanup Complete</b></blockquote>\n\n"
        f"<blockquote>Assistants left <b>{total_left}</b> inactive groups.</blockquote>"
    )
