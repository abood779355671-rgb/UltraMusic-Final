# ==============================================================================
# blacklist.py - User/Chat Blacklist Commands (Sudo Only)
# ==============================================================================
# This plugin manages the bot blacklist to block abusive users/chats.
# Blacklisted entities cannot use the bot.
#
# Commands:
# - حظر_مجموعة [chat_id] - Add chat to blacklist
# - الغاء_حظر_مجموعة [chat_id] - Remove chat from blacklist
# - المجموعات_المحظورة - Show all blacklisted chats
# 
# - حظر [user_id|@username] - Block user
# - الغاء_الحظر [user_id|@username] - Unblock user
# - المحظورين - Show all blocked users
#
# Only sudo users can manage the blacklist.
# ==============================================================================

from pyrogram import filters, types

from UltraMusic import app, db, lang
from UltraMusic.helpers import command


#  ============== CHAT BLACKLIST COMMANDS ==============

@app.on_message(command(["حظر_مجموعة"]) & app.sudo_filter)
@lang.language()
async def _blacklist_chat(_, m: types.Message):
    """Add chat to blacklist."""
    # Auto-delete command message
    try:
        await m.delete()
    except Exception:
        pass
    
    if len(m.command) < 2:
        return await m.reply_text(
            "<blockquote><b>الاستخدام:</b>\n"
            "<code>حظر_مجموعة [معرّف_المجموعة]</code></blockquote>"
        )

    try:
        chat_id = int(m.command[1])
        chat = await app.get_chat(chat_id)
    except ValueError:
        return await m.reply_text("<blockquote>❌ معرّف مجموعة غير صالح</blockquote>")
    except Exception:
        return await m.reply_text("<blockquote>❌ المجموعة غير موجودة</blockquote>")

    if chat_id in db.blacklisted:
        return await m.reply_text(
            f"<blockquote>⚠️ {chat.title} محظورة بالفعل</blockquote>"
        )

    await db.add_blacklist(chat_id)
    await m.reply_text(
        f"<blockquote><u><b>✅ تم حظر المجموعة</b></u>\n\n"
        f"<b>المجموعة:</b> {chat.title}\n"
        f"<b>المعرّف:</b> <code>{chat_id}</code></blockquote>"
    )


@app.on_message(command(["الغاء_حظر_مجموعة"]) & app.sudo_filter)
@lang.language()
async def _whitelist_chat(_, m: types.Message):
    """Remove chat from blacklist."""
    # Auto-delete command message
    try:
        await m.delete()
    except Exception:
        pass
    
    if len(m.command) < 2:
        return await m.reply_text(
            "<blockquote><b>الاستخدام:</b>\n"
            "<code>الغاء_حظر_مجموعة [معرّف_المجموعة]</code></blockquote>"
        )

    try:
        chat_id = int(m.command[1])
        try:
            chat = await app.get_chat(chat_id)
            chat_name = chat.title
        except:
            chat_name = f"مجموعة {chat_id}"
    except ValueError:
        return await m.reply_text("<blockquote>❌ معرّف مجموعة غير صالح</blockquote>")

    if chat_id not in db.blacklisted:
        return await m.reply_text(
            f"<blockquote>⚠️ {chat_name} غير محظورة</blockquote>"
        )

    await db.del_blacklist(chat_id)
    await m.reply_text(
        f"<blockquote><u><b>✅ تم إلغاء حظر المجموعة</b></u>\n\n"
        f"<b>المجموعة:</b> {chat_name}\n"
        f"<b>المعرّف:</b> <code>{chat_id}</code></blockquote>"
    )


@app.on_message(command(["المجموعات_المحظورة"]) & app.sudo_filter)
@lang.language()
async def _blacklisted_chats(_, m: types.Message):
    """Show all blacklisted chats."""
    # Auto-delete command message
    try:
        await m.delete()
    except Exception:
        pass
    
    sent = await m.reply_text("📋 جارٍ جلب المجموعات المحظورة...")
    
    blacklisted = await db.get_blacklisted(chat=True)
    
    # Filter only chats (negative IDs)
    chats_list = [chat_id for chat_id in blacklisted if chat_id < 0]
    
    if not chats_list:
        return await sent.edit_text("<blockquote>✅ لا توجد مجموعات محظورة</blockquote>")
    
    text = "<u><b>🚫 المجموعات المحظورة:</b></u>\n<blockquote>"
    
    for chat_id in chats_list:
        try:
            chat = await app.get_chat(chat_id)
            text += f"\n- {chat.title} ({chat_id})"
        except:
            text += f"\n- مجموعة غير معروفة ({chat_id})"
    
    text += "\n\n</blockquote>"
    await sent.edit_text(text)


# ============== USER BLACKLIST COMMANDS ==============

@app.on_message(command(["حظر"]) & app.sudo_filter)
@lang.language()
async def _block_user(_, m: types.Message):
    """Block a user from using the bot."""
    # Auto-delete command message
    try:
        await m.delete()
    except Exception:
        pass
    
    # Extract user from command or reply
    user_id = None
    
    if m.reply_to_message and m.reply_to_message.from_user:
        user_id = m.reply_to_message.from_user.id
        user_mention = m.reply_to_message.from_user.mention
    elif len(m.command) > 1:
        try:
            user_id = int(m.command[1])
            user = await app.get_users(user_id)
            user_mention = user.mention
        except ValueError:
            return await m.reply_text("<blockquote>❌ معرّف مستخدم غير صالح</blockquote>")
        except Exception:
            return await m.reply_text("<blockquote>❌ المستخدم غير موجود</blockquote>")
    else:
        return await m.reply_text(
            "<blockquote><b>الاستخدام:</b>\n"
            "<code>حظر [معرّف_المستخدم]</code>\n"
            "أو بالرد على رسالة المستخدم</blockquote>"
        )
    
    # Don't allow blocking sudo users
    if user_id in app.sudoers:
        return await m.reply_text("<blockquote>❌ لا يمكن حظر المستخدمين المطورين</blockquote>")
    
    if user_id in app.bl_users:
        return await m.reply_text(
            f"<blockquote>⚠️ {user_mention} محظور بالفعل</blockquote>"
        )

    app.bl_users.add(user_id)
    await db.add_blacklist(user_id)
    await m.reply_text(
        f"<blockquote><u><b>✅ تم حظر المستخدم</b></u>\n\n"
        f"<b>المستخدم:</b> {user_mention}\n"
        f"<b>المعرّف:</b> <code>{user_id}</code></blockquote>"
    )


@app.on_message(command(["الغاء_الحظر"]) & app.sudo_filter)
@lang.language()
async def _unblock_user(_, m: types.Message):
    """Unblock a user."""
    # Auto-delete command message
    try:
        await m.delete()
    except Exception:
        pass
    
    # Extract user from command or reply
    user_id = None
    
    if m.reply_to_message and m.reply_to_message.from_user:
        user_id = m.reply_to_message.from_user.id
        user_mention = m.reply_to_message.from_user.mention
    elif len(m.command) > 1:
        try:
            user_id = int(m.command[1])
            user = await app.get_users(user_id)
            user_mention = user.mention
        except ValueError:
            return await m.reply_text("<blockquote>❌ معرّف مستخدم غير صالح</blockquote>")
        except Exception:
            user_mention = f"مستخدم {user_id}"
    else:
        return await m.reply_text(
            "<blockquote><b>الاستخدام:</b>\n"
            "<code>الغاء_الحظر [معرّف_المستخدم]</code>\n"
            "أو بالرد على رسالة المستخدم</blockquote>"
        )
    
    if user_id not in app.bl_users:
        return await m.reply_text(
            f"<blockquote>⚠️ {user_mention} غير محظور</blockquote>"
        )

    app.bl_users.discard(user_id)
    await db.del_blacklist(user_id)
    await m.reply_text(
        f"<blockquote><u><b>✅ تم إلغاء حظر المستخدم</b></u>\n\n"
        f"<b>المستخدم:</b> {user_mention}\n"
        f"<b>المعرّف:</b> <code>{user_id}</code></blockquote>"
    )


@app.on_message(command(["المحظورين"]) & app.sudo_filter)
@lang.language()
async def _blocked_users(_, m: types.Message):
    """Show all blocked users."""
    # Auto-delete command message
    try:
        await m.delete()
    except Exception:
        pass
    
    sent = await m.reply_text("📋 جارٍ جلب المستخدمين المحظورين...")
    
    blacklisted = await db.get_blacklisted()
    
    if not blacklisted:
        return await sent.edit_text("<blockquote>✅ لا يوجد مستخدمون محظورون</blockquote>")
    
    text = "<u><b>🚫 المستخدمون المحظورون:</b></u>\n<blockquote>"
    
    for user_id in blacklisted:
        try:
            user = await app.get_users(user_id)
            text += f"\n- {user.mention} ({user_id})"
        except:
            text += f"\n- حساب محذوف ({user_id})"
    
    text += "\n\n</blockquote>"
    await sent.edit_text(text)
