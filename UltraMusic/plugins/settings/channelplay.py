# ==============================================================================
# channelplay.py - Channel Play Mode Configuration
# ==============================================================================
# This plugin enables playing music in linked channels instead of the group voice chat.
# Useful for groups with linked channels.
#
# Commands:
# - ربط_القناة linked - Enable for linked channel
# - ربط_القناة <channel_id> - Enable for specific channel
# - ربط_القناة disable - Disable channel play mode
#
# Requirements:
# - User must be admin
# - Bot must be admin in the channel
# - For "linked" mode, channel must be linked to the group
# ==============================================================================

from pyrogram import filters
from pyrogram.enums import ChatMembersFilter, ChatMemberStatus, ChatType
from pyrogram.types import Message

from UltraMusic import app, config, db
from UltraMusic.helpers import command


@app.on_message(command(["ربط_القناة"]) & filters.group & ~app.bl_users)
async def channelplay_command(_, m: Message):
    """Enable or disable channel play mode."""
    # Auto-delete command message
    try:
        await m.delete()
    except Exception:
        pass
    
    # Check if from_user exists (not sent by channel/anonymous admin)
    if not m.from_user:
        return await m.reply_text("❌ لا يمكن استخدام هذا الأمر بواسطة القنوات أو المشرفين المجهولين.")
    
    # Check if user is admin
    member = await app.get_chat_member(m.chat.id, m.from_user.id)
    if member.status not in [ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.OWNER]:
        return await m.reply_text("❌ هذا الأمر للمشرفين فقط.")

    if len(m.command) < 2:
        return await m.reply_text(
            f"إعدادات تشغيل القناة لـ {m.chat.title}\n\n"
            "للتفعيل للقناة المرتبطة:\n"
            "`ربط_القناة linked`\n\n"
            "للتفعيل لأي قناة:\n"
            "`ربط_القناة [معرّف_القناة]`\n\n"
            "لتعطيل تشغيل القناة:\n"
            "`ربط_القناة disable`"
        )

    query = m.text.split(None, 1)[1].strip()

    # Disable channel play
    if query.lower() in ("disable", "تعطيل"):
        await db.set_cmode(m.chat.id, None)
        return await m.reply_text("✅ تم تعطيل تشغيل القناة.")

    # Enable for linked channel
    elif query.lower() in ("linked", "مرتبطة"):
        chat = await app.get_chat(m.chat.id)
        if chat.linked_chat:
            channel_id = chat.linked_chat.id
            await db.set_cmode(m.chat.id, channel_id)
            return await m.reply_text(
                f"✅ تم تفعيل تشغيل القناة لـ: {chat.linked_chat.title}\n"
                f"معرّف القناة: `{chat.linked_chat.id}`"
            )
        else:
            return await m.reply_text("❌ هذه المجموعة لا تملك قناة مرتبطة.")

    # Enable for specific channel
    else:
        # Handle numeric channel IDs
        if query.lstrip("-").isdigit():
            channel_id = int(query)
        else:
            channel_id = query  # Username or invite link

        try:
            chat = await app.get_chat(channel_id)
        except Exception as e:
            return await m.reply_text(
                f"❌ فشل الوصول إلى القناة.\n\n"
                f"الخطأ: `{type(e).__name__}`\n\n"
                "تأكّد من إضافة البوت كمشرف في القناة وترقيته كمشرف.\n\n"
                "للمعرّفات الرقمية: استخدم المعرّف الكامل مع البادئة `-100`\n"
                "مثال: `ربط_القناة -1001234567890`"
            )

        if chat.type != ChatType.CHANNEL:
            return await m.reply_text("❌ القنوات فقط مدعومة.")

        # Check if user is owner of the channel
        owner_username = None
        owner_id = None
        try:
            async for user in app.get_chat_members(
                chat.id, filter=ChatMembersFilter.ADMINISTRATORS
            ):
                if user.status == ChatMemberStatus.OWNER:
                    owner_username = user.user.username or "غير معروف"
                    owner_id = user.user.id
                    break
        except Exception as e:
            return await m.reply_text(
                f"❌ فشل جلب مشرفي القناة.\n\n"
                f"الخطأ: `{type(e).__name__}`\n\n"
                "تأكّد من أن البوت مشرف في القناة."
            )

        if not owner_id:
            return await m.reply_text(
                "❌ تعذّر العثور على مالك القناة.\n\n"
                "تأكّد من أن البوت يملك صلاحية عرض مشرفي القناة."
            )

        if owner_id != m.from_user.id:
            return await m.reply_text(
                f"❌ يجب أن تكون مالك القناة {chat.title} لربطها بهذه المجموعة.\n\n"
                f"مالك القناة: @{owner_username}\n\n"
                "بدلاً من ذلك، يمكنك ربط قناة مجموعتك والاتصال عبر `ربط_القناة linked`"
            )

        await db.set_cmode(m.chat.id, chat.id)
        return await m.reply_text(
            f"✅ تم تفعيل تشغيل القناة لـ: {chat.title}\n"
            f"معرّف القناة: `{chat.id}`"
        )
