# ==============================================================================
# quality.py - Audio & Video Quality Settings
# ==============================================================================
# Commands:
#   /audio_quality  → Show inline buttons to set audio bitrate (admin only)
#   /video_quality  → Show inline buttons to set video quality (admin only)
# Settings are stored per-chat in MongoDB.
# ==============================================================================

from pyrogram import filters
from pyrogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from UltraMusic import app, db
from UltraMusic.helpers import command


# ── helpers ──────────────────────────────────────────────────────────────────

async def _is_admin(message: Message) -> bool:
    """Return True if the sender is a chat admin or the bot owner."""
    admins = await db.get_admins(message.chat.id)
    return message.from_user.id in admins


# ── /audio_quality ────────────────────────────────────────────────────────────

@app.on_message(command(["جودة_الصوت"]) & filters.group)
async def audio_quality_cmd(_, message: Message):
    if not await _is_admin(message):
        return await message.reply(
            "<blockquote>❌ هذا الأمر للمشرفين فقط.</blockquote>",
            parse_mode="html",
        )

    current = await db.get_audio_bitrate(message.chat.id)

    buttons = [
        [
            InlineKeyboardButton("64k" + (" ✓" if current == "64k" else ""), callback_data="aq_64k"),
            InlineKeyboardButton("128k" + (" ✓" if current == "128k" else ""), callback_data="aq_128k"),
        ],
        [
            InlineKeyboardButton("192k" + (" ✓" if current == "192k" else ""), callback_data="aq_192k"),
            InlineKeyboardButton("320k" + (" ✓" if current == "320k" else ""), callback_data="aq_320k"),
        ],
        [InlineKeyboardButton("✖ إغلاق", callback_data="aq_close")],
    ]

    await message.reply(
        f"<blockquote><b>🎵 جودة الصوت</b>\n\nالجودة الحالية: <code>{current}</code>\n\nاختر جودة الصوت للمجموعة:</blockquote>",
        reply_markup=InlineKeyboardMarkup(buttons),
        parse_mode="html",
    )


@app.on_callback_query(filters.regex(r"^aq_"))
async def audio_quality_cb(_, query: CallbackQuery):
    admins = await db.get_admins(query.message.chat.id)
    if query.from_user.id not in admins:
        return await query.answer("❌ للمشرفين فقط!", show_alert=True)

    data = query.data  # e.g. "aq_128k" or "aq_close"

    if data == "aq_close":
        await query.message.delete()
        return

    bitrate_map = {"aq_64k": "64k", "aq_128k": "128k", "aq_192k": "192k", "aq_320k": "320k"}
    bitrate = bitrate_map.get(data)
    if not bitrate:
        return await query.answer("خيار غير صالح.", show_alert=True)

    await db.set_audio_bitrate(query.message.chat.id, bitrate)

    buttons = [
        [
            InlineKeyboardButton("64k" + (" ✓" if bitrate == "64k" else ""), callback_data="aq_64k"),
            InlineKeyboardButton("128k" + (" ✓" if bitrate == "128k" else ""), callback_data="aq_128k"),
        ],
        [
            InlineKeyboardButton("192k" + (" ✓" if bitrate == "192k" else ""), callback_data="aq_192k"),
            InlineKeyboardButton("320k" + (" ✓" if bitrate == "320k" else ""), callback_data="aq_320k"),
        ],
        [InlineKeyboardButton("✖ إغلاق", callback_data="aq_close")],
    ]

    await query.message.edit_text(
        f"<blockquote><b>🎵 جودة الصوت</b>\n\nالجودة الحالية: <code>{bitrate}</code>\n\n✅ تم الحفظ بنجاح.</blockquote>",
        reply_markup=InlineKeyboardMarkup(buttons),
        parse_mode="html",
    )
    await query.answer(f"✅ تم تعيين جودة الصوت إلى {bitrate}")


# ── /video_quality ────────────────────────────────────────────────────────────

@app.on_message(command(["جودة_الفيديو"]) & filters.group)
async def video_quality_cmd(_, message: Message):
    if not await _is_admin(message):
        return await message.reply(
            "<blockquote>❌ هذا الأمر للمشرفين فقط.</blockquote>",
            parse_mode="html",
        )

    current = await db.get_video_quality(message.chat.id)

    buttons = [
        [
            InlineKeyboardButton("480p" + (" ✓" if current == "480" else ""), callback_data="vq_480"),
            InlineKeyboardButton("720p" + (" ✓" if current == "720" else ""), callback_data="vq_720"),
            InlineKeyboardButton("1080p" + (" ✓" if current == "1080" else ""), callback_data="vq_1080"),
        ],
        [InlineKeyboardButton("✖ إغلاق", callback_data="vq_close")],
    ]

    await message.reply(
        f"<blockquote><b>🎬 جودة الفيديو</b>\n\nالجودة الحالية: <code>{current}p</code>\n\nاختر جودة الفيديو للمجموعة:</blockquote>",
        reply_markup=InlineKeyboardMarkup(buttons),
        parse_mode="html",
    )


@app.on_callback_query(filters.regex(r"^vq_"))
async def video_quality_cb(_, query: CallbackQuery):
    admins = await db.get_admins(query.message.chat.id)
    if query.from_user.id not in admins:
        return await query.answer("❌ للمشرفين فقط!", show_alert=True)

    data = query.data

    if data == "vq_close":
        await query.message.delete()
        return

    quality_map = {"vq_480": "480", "vq_720": "720", "vq_1080": "1080"}
    quality = quality_map.get(data)
    if not quality:
        return await query.answer("خيار غير صالح.", show_alert=True)

    await db.set_video_quality(query.message.chat.id, quality)

    buttons = [
        [
            InlineKeyboardButton("480p" + (" ✓" if quality == "480" else ""), callback_data="vq_480"),
            InlineKeyboardButton("720p" + (" ✓" if quality == "720" else ""), callback_data="vq_720"),
            InlineKeyboardButton("1080p" + (" ✓" if quality == "1080" else ""), callback_data="vq_1080"),
        ],
        [InlineKeyboardButton("✖ إغلاق", callback_data="vq_close")],
    ]

    await query.message.edit_text(
        f"<blockquote><b>🎬 جودة الفيديو</b>\n\nالجودة الحالية: <code>{quality}p</code>\n\n✅ تم الحفظ بنجاح.</blockquote>",
        reply_markup=InlineKeyboardMarkup(buttons),
        parse_mode="html",
    )
    await query.answer(f"✅ تم تعيين جودة الفيديو إلى {quality}p")
