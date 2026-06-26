# ==============================================================================
# commands_list.py - Command List ("الأوامر")
# ==============================================================================
# This plugin handles:
# - أوامر / الاوامر / commands - shows every Arabic command the bot accepts,
#   grouped by category, without the "/" prefix.
# ==============================================================================

from pyrogram import enums, types

from UltraMusic import app, lang
from UltraMusic.helpers import command


COMMANDS_TEXT = (
    "⚡️ <b>قائمة أوامر البوت</b>\n\n"

    "<blockquote expandable><b>🎵 التشغيل</b>\n"
    "تشغيل [اسم الأغنية أو رابط]\n"
    "تشغيل_فوري [اسم الأغنية أو رابط]\n"
    "فيديو [اسم الأغنية أو رابط]\n"
    "فيديو_فوري [اسم الأغنية أو رابط]\n"
    "تشغيل_قناة [اسم الأغنية أو رابط]\n"
    "تشغيل_قناة_فوري [اسم الأغنية أو رابط]\n"
    "فيديو_قناة [اسم الأغنية أو رابط]\n"
    "فيديو_قناة_فوري [اسم الأغنية أو رابط]\n"
    "بحث [اسم الأغنية]</blockquote>\n\n"

    "<blockquote expandable><b>🎛️ التحكم بالتشغيل</b>\n"
    "تجميد\n"
    "استمرار\n"
    "تخطي\n"
    "ايقاف\n"
    "كتم\n"
    "الغاء_الكتم\n"
    "عشوائي\n"
    "تكرار\n"
    "تقديم / ترجيع\n"
    "القائمة</blockquote>\n\n"

    "<blockquote expandable><b>⚙️ إعدادات المجموعة</b>\n"
    "تصريح\n"
    "الغاء_التصريح\n"
    "قائمة_التصريح\n"
    "تحديث_المشرفين\n"
    "ربط_القناة\n"
    "جودة_الصوت\n"
    "جودة_الفيديو\n"
    "بدء (مع settings/playmode)</blockquote>"
)


@app.on_message(command(["الاوامر", "الأوامر", "اوامر"]) & ~app.bl_users)
@lang.language()
async def _commands_list(_, m: types.Message):
    """Show the full list of Arabic bot commands (no "/" prefix)."""
    # Auto-delete command message in group chats
    if m.chat.type != enums.ChatType.PRIVATE:
        try:
            await m.delete()
        except Exception:
            pass

    try:
        await m.reply_text(COMMANDS_TEXT, quote=True)
    except Exception:
        pass
