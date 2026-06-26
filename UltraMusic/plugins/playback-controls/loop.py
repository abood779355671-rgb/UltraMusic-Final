# ==============================================================================
# loop.py - Loop Mode Command
# ==============================================================================
# This plugin handles loop mode management.
#
# Commands:
# - /loop - Cycle through loop modes (disable -> single -> queue -> disable)
# - /loop disable - Disable loop
# - /loop single - Loop current track
# - /loop queue - Loop entire queue
#
# Requirements:
# - User must be admin or authorized user
# ==============================================================================

from pyrogram import filters, types

from UltraMusic import app, db, lang
from UltraMusic.helpers import can_manage_vc, command


@app.on_message(command(["تكرار"]) & filters.group & ~app.bl_users)
@lang.language()
@can_manage_vc
async def _loop(_, m: types.Message):
    # Auto-delete command message
    try:
        await m.delete()
    except Exception:
        pass
    
    current_loop = await db.get_loop(m.chat.id)
    
    # Check if user specified a mode
    if len(m.command) > 1:
        mode_arg = m.command[1].lower()
        if mode_arg in ["0", "disable", "تعطيل", "ايقاف"]:
            new_loop = 0
            text = "<blockquote>➡️ تم تعطيل وضع التكرار</blockquote>"
        elif mode_arg in ["single", "1", "one", "واحد", "مقطع"]:
            new_loop = 1
            text = "<blockquote>🔂 تم ضبط التكرار على المقطع الحالي</blockquote>"
        elif mode_arg in ["queue", "all", "10", "القائمة", "الكل"]:
            new_loop = 10
            text = "<blockquote>🔁 تم ضبط التكرار على القائمة كاملة</blockquote>"
        else:
            return await m.reply_text(
                "<blockquote><b>الاستخدام:</b>\n"
                "• تكرار - التنقّل بين الأوضاع\n"
                "• تكرار تعطيل - إيقاف التكرار\n"
                "• تكرار مقطع - تكرار المقطع الحالي\n"
                "• تكرار القائمة - تكرار القائمة كاملة</blockquote>"
            )
    else:
        # Cycle through modes
        if current_loop == 0:
            new_loop = 1
            text = "<blockquote>🔂 تم ضبط التكرار على المقطع الحالي</blockquote>"
        elif current_loop == 1:
            new_loop = 10
            text = "<blockquote>🔁 تم ضبط التكرار على القائمة كاملة</blockquote>"
        else:
            new_loop = 0
            text = "<blockquote>➡️ تم تعطيل وضع التكرار</blockquote>"
    
    await db.set_loop(m.chat.id, new_loop)
    await m.reply_text(text)
