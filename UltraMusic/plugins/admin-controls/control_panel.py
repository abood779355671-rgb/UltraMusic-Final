# ==============================================================================
# control_panel.py - لوحة تحكم المالك (Owner Control Panel)
# ==============================================================================
# الأوامر:
# - /لوحة_التحكم — يفتح القائمة الرئيسية للوحة التحكم
#
# الصلاحيات:
# - المالك (config.OWNER_ID) أو أحد app.sudoers فقط
#
# نمط callback_data:
# - panel:main                       → القائمة الرئيسية
# - panel:settings                   → قسم الإعدادات العامة
# - panel:settings:set:DURATION_LIMIT      → تعديل قيمة رقمية
# - panel:settings:set:QUEUE_LIMIT
# - panel:settings:set:PLAYLIST_LIMIT
# - panel:settings:set:SONG_DOWNLOAD_LIMIT
# - panel:settings:toggle:AUTO_END         → تبديل فوري
# - panel:settings:toggle:AUTO_LEAVE
# - panel:settings:toggle:VIDEO_PLAY
# - panel:settings:toggle:auto_leave_empty → سويتش الخلو من المستخدمين
# - panel:broadcast                  → قسم البرودكاست (توجيهي فقط، بدون تنفيذ)
# - panel:leave                      → قسم مغادرة المجموعات
# - panel:leave:specific             → مغادرة مجموعة محدّدة عبر chat_id
# - panel:leave:idle / :idle:yes     → مغادرة المجموعات الخاملة (تأكيد ثم تنفيذ)
# ==============================================================================

import asyncio
import html
import os
import time
from pathlib import Path

from pyrogram import filters, types

from UltraMusic import app, boot, config, db, logger, userbot, yt
from UltraMusic import tune as _tune_instance  # for cancel_all_auto_leave_tasks
from UltraMusic.helpers import command

# ------------------------------------------------------------------------------
# ثوابت
# ------------------------------------------------------------------------------

# مفاتيح الإعدادات الرقمية: (مفتاح DB, وحدة العرض, وحدة التخزين الداخلية)
# stored_unit="seconds"  → القيمة تُخزَّن بالثواني، المستخدم يُدخل بالدقائق
# stored_unit="count"    → القيمة تُخزَّن مباشرةً كما يُدخلها المستخدم
_NUMERIC_SETTINGS = {
    "DURATION_LIMIT": {
        "label": "⏱ مدة التشغيل القصوى",
        "stored_unit": "seconds",   # تُخزَّن بالثواني
        "display_unit": "دقيقة",
        "config_attr": "DURATION_LIMIT",
    },
    "QUEUE_LIMIT": {
        "label": "📋 حد قائمة التشغيل",
        "stored_unit": "count",
        "display_unit": "مقطع",
        "config_attr": "QUEUE_LIMIT",
    },
    "PLAYLIST_LIMIT": {
        "label": "🎵 حد قوائم يوتيوب",
        "stored_unit": "count",
        "display_unit": "مقطع",
        "config_attr": "PLAYLIST_LIMIT",
    },
    "SONG_DOWNLOAD_LIMIT": {
        "label": "⬇️ حد تحميل الأغاني",
        "stored_unit": "seconds",   # تُخزَّن بالثواني
        "display_unit": "دقيقة",
        "config_attr": "SONG_DOWNLOAD_LIMIT",
    },
}

# مفاتيح الإعدادات المنطقية (تبديل فوري)
_TOGGLE_SETTINGS = {
    "AUTO_END": {
        "label": "🔚 إنهاء تلقائي عند فراغ القائمة",
        "config_attr": "AUTO_END",
        "use_db_setting": True,    # يُخزَّن عبر db.get_setting / db.set_setting
    },
    "AUTO_LEAVE": {
        "label": "🚶 مغادرة تلقائية عند الصمت",
        "config_attr": "AUTO_LEAVE",
        "use_db_setting": True,
    },
    "VIDEO_PLAY": {
        "label": "🎬 تشغيل الفيديو (/vplay)",
        "config_attr": "VIDEO_PLAY",
        "use_db_setting": False,   # يستخدم db.set_vplay_enabled الموجودة
    },
}

# مهلة انتظار إدخال المستخدم (ثانية)
_INPUT_TIMEOUT = 120

# خيارات الجودة الافتراضية (تُطبَّق على كل مجموعة جديدة لم تُخصِّص قيمة بعد)
_AUDIO_BITRATE_CHOICES = ["64k", "128k", "192k", "320k"]
_VIDEO_QUALITY_CHOICES = ["480", "720", "1080"]


# ------------------------------------------------------------------------------
# دوال مساعدة لقراءة القيم الحالية
# ------------------------------------------------------------------------------

async def _get_numeric_value(key: str) -> int:
    """اقرأ القيمة الحالية للإعداد الرقمي من DB مع fallback لـ config."""
    cfg_val = getattr(config, _NUMERIC_SETTINGS[key]["config_attr"])
    try:
        return await db.get_setting(key, cfg_val)
    except Exception as e:
        logger.warning(f"[control_panel] تعذّر قراءة {key} من DB: {e}")
        return cfg_val


async def _get_toggle_value(key: str) -> bool:
    """اقرأ القيمة الحالية للإعداد المنطقي من DB مع fallback لـ config."""
    meta = _TOGGLE_SETTINGS[key]
    cfg_val = getattr(config, meta["config_attr"])
    if key == "VIDEO_PLAY":
        try:
            return await db.get_vplay_enabled()
        except Exception as e:
            logger.warning(f"[control_panel] تعذّر قراءة VIDEO_PLAY من DB: {e}")
            return cfg_val
    else:
        try:
            return await db.get_setting(key, cfg_val)
        except Exception as e:
            logger.warning(f"[control_panel] تعذّر قراءة {key} من DB: {e}")
            return cfg_val


def _display_numeric(key: str, raw_value: int) -> str:
    """حوّل القيمة الخام للعرض (ثواني → دقائق عند الحاجة)."""
    meta = _NUMERIC_SETTINGS[key]
    if meta["stored_unit"] == "seconds":
        return str(raw_value // 60)
    return str(raw_value)


# ------------------------------------------------------------------------------
# دوال بناء لوحات المفاتيح
# ------------------------------------------------------------------------------

def _back_row(parent: str) -> list:
    """صف التنقّل الموحّد: «رجوع» دائماً + «الرئيسية» إذا لم نكن في المستوى الأول."""
    row = [types.InlineKeyboardButton("⬅️ رجوع", callback_data=parent)]
    if parent != "panel:main":
        row.append(types.InlineKeyboardButton("🏠 الرئيسية", callback_data="panel:main"))
    return row


def _main_keyboard() -> types.InlineKeyboardMarkup:
    """القائمة الرئيسية — الزر الحقيقي لقسم الإعدادات."""
    return types.InlineKeyboardMarkup([
        [types.InlineKeyboardButton("⚙️ الإعدادات العامة",       callback_data="panel:settings")],
        [types.InlineKeyboardButton("🎨 واجهة التشغيل",          callback_data="panel:ui")],
        [types.InlineKeyboardButton("🎨 الأنماط والأشكال",       callback_data="panel:themes")],
        [types.InlineKeyboardButton("🌐 اللغة والترجمة",         callback_data="panel:language")],
        [types.InlineKeyboardButton("🤖 المساعدون",              callback_data="panel:assistants")],
        [types.InlineKeyboardButton("🔑 مفاتيح API والكوكيز",   callback_data="panel:api")],
        [types.InlineKeyboardButton("🛠️ الصيانة والنظام",        callback_data="panel:system")],
        [types.InlineKeyboardButton("🚫 الحظر والصلاحيات",        callback_data="panel:bans")],
        [types.InlineKeyboardButton("📢 البرودكاست",              callback_data="panel:broadcast")],
        [types.InlineKeyboardButton("🚪 مغادرة المجموعات",        callback_data="panel:leave")],
    ])


async def _settings_keyboard() -> types.InlineKeyboardMarkup:
    """لوحة مفاتيح شاشة الإعدادات العامة مع القيم الحالية."""
    rows = []

    # ── الإعدادات الرقمية ──────────────────────────────────────────────────
    for key, meta in _NUMERIC_SETTINGS.items():
        val = await _get_numeric_value(key)
        display = _display_numeric(key, val)
        rows.append([
            types.InlineKeyboardButton(
                f"{meta['label']}: {display} {meta['display_unit']}",
                callback_data=f"panel:settings:set:{key}"
            )
        ])

    # ── الإعدادات المنطقية (تبديل فوري) ───────────────────────────────────
    for key, meta in _TOGGLE_SETTINGS.items():
        val = await _get_toggle_value(key)
        icon = "🟢" if val else "🔴"
        rows.append([
            types.InlineKeyboardButton(
                f"{icon} {meta['label']}",
                callback_data=f"panel:settings:toggle:{key}"
            )
        ])

    # ── سويتش الخلو من المستخدمين ──────────────────────────────────────────
    try:
        leave_empty = await db.get_setting("auto_leave_empty_enabled", True)
    except Exception as e:
        logger.warning(f"[control_panel] تعذّر قراءة auto_leave_empty_enabled: {e}")
        leave_empty = True
    leave_icon = "🟢" if leave_empty else "🔴"
    rows.append([
        types.InlineKeyboardButton(
            f"{leave_icon} 🚪 مغادرة عند الخلو من المستخدمين",
            callback_data="panel:settings:toggle:auto_leave_empty"
        )
    ])

    # ── الجودة الافتراضية للمجموعات الجديدة ────────────────────────────────
    try:
        default_audio = await db.get_default_audio_bitrate()
    except Exception as e:
        logger.warning(f"[control_panel] تعذّر قراءة default_audio_bitrate: {e}")
        default_audio = "128k"
    try:
        default_video = await db.get_default_video_quality()
    except Exception as e:
        logger.warning(f"[control_panel] تعذّر قراءة default_video_quality: {e}")
        default_video = "720"
    rows.append([
        types.InlineKeyboardButton(
            f"🎵 جودة الصوت الافتراضية: {default_audio}",
            callback_data="panel:settings:quality:audio",
        )
    ])
    rows.append([
        types.InlineKeyboardButton(
            f"🎬 جودة الفيديو الافتراضية: {default_video}p",
            callback_data="panel:settings:quality:video",
        )
    ])

    # ── الوضع النظيف (عرض إعلامي فقط — إعداد per-chat) ─────────────────────
    rows.append([
        types.InlineKeyboardButton(
            "🧹 الوضع النظيف (Clean Mode) — كيف يُفعَّل؟",
            callback_data="panel:settings:cleanmode_info",
        )
    ])

    rows.append(_back_row("panel:main"))
    return types.InlineKeyboardMarkup(rows)


# ── الجودة الافتراضية للمجموعات الجديدة (Audio/Video) ──────────────────────

async def _quality_audio_keyboard() -> types.InlineKeyboardMarkup:
    """لوحة مفاتيح اختيار الجودة الصوتية الافتراضية العامة."""
    current = await db.get_default_audio_bitrate()
    row = []
    for choice in _AUDIO_BITRATE_CHOICES:
        mark = " ✓" if choice == current else ""
        row.append(types.InlineKeyboardButton(
            f"{choice}{mark}", callback_data=f"panel:settings:quality:audio:set:{choice}"
        ))
    return types.InlineKeyboardMarkup([row, _back_row("panel:settings")])


async def _quality_video_keyboard() -> types.InlineKeyboardMarkup:
    """لوحة مفاتيح اختيار جودة الفيديو الافتراضية العامة."""
    current = await db.get_default_video_quality()
    row = []
    for choice in _VIDEO_QUALITY_CHOICES:
        mark = " ✓" if choice == current else ""
        row.append(types.InlineKeyboardButton(
            f"{choice}p{mark}", callback_data=f"panel:settings:quality:video:set:{choice}"
        ))
    return types.InlineKeyboardMarkup([row, _back_row("panel:settings")])


async def _handle_quality_audio_show(query: types.CallbackQuery) -> None:
    """اعرض شاشة اختيار الجودة الصوتية الافتراضية."""
    await query.answer()
    current = await db.get_default_audio_bitrate()
    await query.edit_message_text(
        "🎵 <b>الجودة الصوتية الافتراضية</b>\n\n"
        f"الجودة الحالية: <code>{current}</code>\n\n"
        "ℹ️ تُطبَّق هذه القيمة تلقائياً على أي مجموعة جديدة لم تُخصِّص "
        "جودة صوت خاصة بها عبر /جودة_الصوت داخل المجموعة. "
        "المجموعات التي خصّصت قيمة بالفعل لن تتأثر.\n\n"
        "اختر الجودة الافتراضية الجديدة:",
        reply_markup=await _quality_audio_keyboard(),
    )


async def _handle_quality_video_show(query: types.CallbackQuery) -> None:
    """اعرض شاشة اختيار جودة الفيديو الافتراضية."""
    await query.answer()
    current = await db.get_default_video_quality()
    await query.edit_message_text(
        "🎬 <b>جودة الفيديو الافتراضية</b>\n\n"
        f"الجودة الحالية: <code>{current}p</code>\n\n"
        "ℹ️ تُطبَّق هذه القيمة تلقائياً على أي مجموعة جديدة لم تُخصِّص "
        "جودة فيديو خاصة بها عبر /جودة_الفيديو داخل المجموعة. "
        "المجموعات التي خصّصت قيمة بالفعل لن تتأثر.\n\n"
        "اختر الجودة الافتراضية الجديدة:",
        reply_markup=await _quality_video_keyboard(),
    )


async def _handle_quality_audio_set(query: types.CallbackQuery, value: str) -> None:
    """احفظ الجودة الصوتية الافتراضية الجديدة."""
    if value not in _AUDIO_BITRATE_CHOICES:
        return await query.answer("خيار غير صالح.", show_alert=True)
    try:
        await db.set_default_audio_bitrate(value)
        logger.info(f"[control_panel] default_audio_bitrate ← {value} بواسطة {query.from_user.id}")
    except Exception as e:
        logger.error(f"[control_panel] فشل حفظ default_audio_bitrate: {e}", exc_info=True)
        return await query.answer("❌ فشل الحفظ في قاعدة البيانات.", show_alert=True)
    await query.answer(f"✅ تم تعيين الجودة الصوتية الافتراضية إلى {value}")
    await query.edit_message_text(
        "🎵 <b>الجودة الصوتية الافتراضية</b>\n\n"
        f"✅ تم الحفظ. الجودة الحالية: <code>{value}</code>\n\n"
        "ℹ️ تُطبَّق هذه القيمة تلقائياً على أي مجموعة جديدة لم تُخصِّص "
        "جودة صوت خاصة بها.",
        reply_markup=await _quality_audio_keyboard(),
    )


async def _handle_quality_video_set(query: types.CallbackQuery, value: str) -> None:
    """احفظ جودة الفيديو الافتراضية الجديدة."""
    if value not in _VIDEO_QUALITY_CHOICES:
        return await query.answer("خيار غير صالح.", show_alert=True)
    try:
        await db.set_default_video_quality(value)
        logger.info(f"[control_panel] default_video_quality ← {value} بواسطة {query.from_user.id}")
    except Exception as e:
        logger.error(f"[control_panel] فشل حفظ default_video_quality: {e}", exc_info=True)
        return await query.answer("❌ فشل الحفظ في قاعدة البيانات.", show_alert=True)
    await query.answer(f"✅ تم تعيين جودة الفيديو الافتراضية إلى {value}p")
    await query.edit_message_text(
        "🎬 <b>جودة الفيديو الافتراضية</b>\n\n"
        f"✅ تم الحفظ. الجودة الحالية: <code>{value}p</code>\n\n"
        "ℹ️ تُطبَّق هذه القيمة تلقائياً على أي مجموعة جديدة لم تُخصِّص "
        "جودة فيديو خاصة بها.",
        reply_markup=await _quality_video_keyboard(),
    )


async def _ui_keyboard() -> types.InlineKeyboardMarkup:
    """لوحة مفاتيح شاشة واجهة التشغيل مع القيم الحالية."""
    rows = []

    # ── زر 1: تفعيل/تعطيل الصورة المصغّرة ──────────────────────────────────
    try:
        thumb_gen = await db.get_setting("thumb_gen", config.THUMB_GEN)
    except Exception as e:
        logger.warning(f"[control_panel] تعذّر قراءة thumb_gen: {e}")
        thumb_gen = config.THUMB_GEN
    thumb_icon = "🟢" if thumb_gen else "🔴"
    rows.append([
        types.InlineKeyboardButton(
            f"{thumb_icon} الصورة المصغّرة التلقائية (THUMB_GEN)",
            callback_data="panel:ui:toggle:thumb_gen",
        )
    ])

    # ── زر 2: رفع/تغيير الصورة الافتراضية ───────────────────────────────────
    try:
        has_custom = bool(await db.get_setting("default_thumb_file_id", None))
    except Exception as e:
        logger.warning(f"[control_panel] تعذّر قراءة default_thumb_file_id: {e}")
        has_custom = False
    thumb_label = "🖼 الصورة الافتراضية: مخصّصة ✅" if has_custom else "🖼 الصورة الافتراضية: الإعداد الأصلي"
    rows.append([
        types.InlineKeyboardButton(
            thumb_label,
            callback_data="panel:ui:set:default_thumb",
        )
    ])

    # ── زر 3: تعديل نص قالب رسالة التشغيل ──────────────────────────────────
    try:
        has_template = bool(await db.get_setting("play_media_template", None))
    except Exception as e:
        logger.warning(f"[control_panel] تعذّر قراءة play_media_template: {e}")
        has_template = False
    tmpl_label = "✏️ قالب رسالة التشغيل: مخصّص ✅" if has_template else "✏️ قالب رسالة التشغيل: الافتراضي"
    rows.append([
        types.InlineKeyboardButton(
            tmpl_label,
            callback_data="panel:ui:set:play_template",
        )
    ])

    # ── زر 4: تبديل تخطيط الأزرار ────────────────────────────────────────────
    try:
        layout = await db.get_setting("button_layout", "compact")
        if layout not in ("compact", "expanded"):
            layout = "compact"
    except Exception as e:
        logger.warning(f"[control_panel] تعذّر قراءة button_layout: {e}")
        layout = "compact"
    layout_label = (
        "📐 تخطيط الأزرار: مضغوط — صف واحد 🔳"
        if layout == "compact"
        else "📐 تخطيط الأزرار: موسّع — صفّان 🔲"
    )
    rows.append([
        types.InlineKeyboardButton(
            layout_label,
            callback_data="panel:ui:toggle:button_layout",
        )
    ])

    rows.append(_back_row("panel:main"))
    return types.InlineKeyboardMarkup(rows)

MAIN_TEXT = (
    "🎛 <b>لوحة تحكم UltraMusic</b>\n\n"
    "مرحباً بك في لوحة التحكم الرئيسية.\n"
    "اختر أحد الأقسام أدناه:"
)

SETTINGS_TEXT = (
    "⚙️ <b>الإعدادات العامة</b>\n\n"
    "اضغط على أي إعداد لتعديله.\n"
    "• الأزرار الرقمية: ستُطلب إدخال قيمة جديدة.\n"
    "• أزرار 🟢/🔴: تبديل فوري."
)

UI_TEXT = (
    "🎨 <b>واجهة التشغيل</b>\n\n"
    "تحكّم في مظهر رسائل التشغيل:\n"
    "• 🟢/🔴 تبديل فوري.\n"
    "• أزرار الرفع والتعديل: ستُطلب منك إدخالاً."
)

LANGUAGE_TEXT = (
    "🌐 <b>اللغة والترجمة</b>\n\n"
    "اللغة العربية هي اللغة الوحيدة المتاحة حالياً.\n\n"
    "📌 دعم لغات إضافية سيُضاف في إصدارات قادمة."
)

ASSISTANTS_TEXT = "🤖 <b>المساعدون</b>\n\nجارٍ تحميل بيانات المساعدين..."

BROADCAST_TEXT = (
    "📢 <b>البرودكاست</b>\n\n"
    "لأسباب تتعلق بالسلامة، إرسال البرودكاست لا يُنفَّذ من داخل لوحة التحكم.\n"
    "الأمر الحالي يدعم الردّ على رسائل وسائط (بما فيها الألبومات) وعدة خيارات "
    "(تثبيت، نسخ بدون علامة توجيه، استثناء المجموعات...)، وتنفيذها بأمان داخل "
    "آلية «انتظار رد» في اللوحة قد يُفقد بعض هذه الخيارات أو يتسبب بسلوك غير متوقع.\n\n"
    "استخدم الأمر مباشرةً في الخاص مع البوت:\n\n"
    "• <code>/broadcast &lt;نص&gt;</code> — إرسال نص لكل المجموعات\n"
    "• <code>/broadcast -user &lt;نص&gt;</code> — إرسال للمجموعات والمستخدمين معاً\n"
    "• <code>/broadcast -nochat -user &lt;نص&gt;</code> — للمستخدمين فقط\n"
    "• <code>/broadcast -pin</code> أو <code>-pinloud</code> — تثبيت الرسالة بعد الإرسال\n"
    "• <code>/broadcast -copy</code> — إرسال كنسخة بدون علامة «توجيه»\n"
    "• الرد على رسالة وسائط (صورة/فيديو/ألبوم) عند تنفيذ الأمر يُرسلها كما هي\n"
    "• <code>/stop_gcast</code> أو <code>/stop_broadcast</code> — إيقاف برودكاست جارٍ"
)

LEAVE_TEXT = (
    "🚪 <b>مغادرة المجموعات</b>\n\n"
    "اختر إجراءً:"
)

# ==============================================================================
# قسم 🎨 الأنماط والأشكال (panel:themes)
# ==============================================================================

async def _build_themes_text() -> str:
    """بناء نص شاشة الأنماط مع اسم الثيم النشط حالياً."""
    try:
        active_id = await db.get_active_theme()
        if active_id:
            from UltraMusic.helpers._themes import THEMES as _TH  # noqa: PLC0415
            active_name = _TH.get(active_id, {}).get("name_ar") or active_id
        else:
            active_name = "كلاسيكي"
    except Exception as e:
        logger.warning(f"[control_panel] تعذّر قراءة active_theme: {e}")
        active_name = "كلاسيكي"
    return (
        "🎨 <b>الأنماط والأشكال</b>\n"
        f"الثيم النشط حالياً: <b>{active_name}</b>"
    )


async def _themes_keyboard() -> types.InlineKeyboardMarkup:
    """لوحة مفاتيح شاشة الأنماط والأشكال."""
    from UltraMusic.helpers._themes import THEMES as _TH  # noqa: PLC0415
    rows = []

    # ── أزرار الثيمات المدمجة الأربعة ──────────────────────────────────────
    for theme_id, theme in _TH.items():
        rows.append([
            types.InlineKeyboardButton(
                theme["name_ar"],
                callback_data=f"panel:themes:preview:{theme_id}",
            )
        ])

    # ── أزرار الثيمات المخصصة (إن وجدت) ────────────────────────────────────
    try:
        custom_themes = await db.list_custom_themes()
        for ct in custom_themes:
            ct_id   = ct.get("id", "")
            ct_name = ct.get("name_ar") or ct_id
            if ct_id:
                rows.append([
                    types.InlineKeyboardButton(
                        ct_name,
                        callback_data=f"panel:themes:preview:{ct_id}",
                    )
                ])
    except Exception as e:
        logger.warning(f"[control_panel] تعذّر قراءة list_custom_themes: {e}")
        # لا تعرض أزراراً إضافية فقط

    # ── زر إنشاء ثيم مخصص جديد ──────────────────────────────────────────────
    rows.append([
        types.InlineKeyboardButton(
            "➕ إنشاء ثيم مخصص جديد",
            callback_data="panel:themes:create",
        )
    ])

    # ── صف التنقّل الأخير ────────────────────────────────────────────────────
    rows.append([
        types.InlineKeyboardButton("⬅️ رجوع",      callback_data="panel:main"),
        types.InlineKeyboardButton("🏠 الرئيسية",   callback_data="panel:main"),
    ])

    return types.InlineKeyboardMarkup(rows)



API_TEXT = "🔑 <b>مفاتيح API والكوكيز</b>\n\nجارٍ تحميل البيانات..."

SYSTEM_TEXT = (
    "🛠️ <b>الصيانة والنظام</b>\n\n"
    "جارٍ تحميل الإحصائيات..."
)


async def _handle_themes_preview(query: types.CallbackQuery, theme_id: str) -> None:
    """شاشة معاينة الثيم — تبني رسالة تجريبية وتعرضها مع زر التفعيل."""
    from UltraMusic.helpers._themes import get_theme, build_now_playing_text  # noqa: PLC0415

    await query.answer()

    # جلب الثيم (يرجع classic تلقائياً عند أي خطأ أو theme_id غير موجود)
    theme = await get_theme(theme_id)

    # ── بيانات وهمية للمعاينة ────────────────────────────────────────────────
    _url      = "https://t.me"
    _title    = "أغنية تجريبية"
    _duration = "3:45"
    _user     = "@تجريبي"

    preview_text = build_now_playing_text(theme, _url, _title, _duration, _user)

    preview_markup = types.InlineKeyboardMarkup([
        [types.InlineKeyboardButton(
            "✅ تفعيل هذا الثيم",
            callback_data=f"panel:themes:activate:{theme_id}",
        )],
        [types.InlineKeyboardButton("⬅️ رجوع", callback_data="panel:themes")],
    ])

    show_thumbnail = theme.get("show_thumbnail", True)

    if show_thumbnail:
        # قراءة الصورة الافتراضية من DB
        try:
            file_id = await db.get_setting("default_thumb_file_id", None)
        except Exception as e:
            logger.warning(f"[control_panel] تعذّر قراءة default_thumb_file_id: {e}")
            file_id = None

        if file_id:
            # احذف الرسالة الحالية وأرسل صورة جديدة
            try:
                await query.message.delete()
            except Exception:
                pass
            try:
                await app.send_photo(
                    chat_id=query.message.chat.id,
                    photo=file_id,
                    caption=preview_text,
                    reply_markup=preview_markup,
                )
            except Exception as e:
                logger.warning(f"[control_panel] فشل send_photo للمعاينة: {e}")
                await app.send_message(
                    chat_id=query.message.chat.id,
                    text=preview_text + "\n\n⚠️ <i>تعذّر تحميل الصورة الافتراضية.</i>",
                    reply_markup=preview_markup,
                )
        else:
            # لا توجد صورة افتراضية مُحدَّدة بعد
            note = "\n\n⚠️ <i>لا توجد صورة افتراضية مُحدَّدة بعد.</i>"
            try:
                await query.edit_message_text(
                    text=preview_text + note,
                    reply_markup=preview_markup,
                )
            except Exception:
                try:
                    await query.message.delete()
                except Exception:
                    pass
                await app.send_message(
                    chat_id=query.message.chat.id,
                    text=preview_text + note,
                    reply_markup=preview_markup,
                )
    else:
        # show_thumbnail = False → نص فقط بدون صورة
        try:
            await query.edit_message_text(
                text=preview_text,
                reply_markup=preview_markup,
            )
        except Exception:
            try:
                await query.message.delete()
            except Exception:
                pass
            await app.send_message(
                chat_id=query.message.chat.id,
                text=preview_text,
                reply_markup=preview_markup,
            )


async def _handle_themes_activate(query: types.CallbackQuery, theme_id: str) -> None:
    """تفعيل الثيم المحدد وتأكيد العملية للمستخدم."""
    from UltraMusic.helpers._themes import get_theme  # noqa: PLC0415

    theme = await get_theme(theme_id)
    name_ar = theme.get("name_ar", theme_id)

    try:
        await db.set_active_theme(theme_id)
        logger.info(
            f"[control_panel] active_theme → {theme_id} ({name_ar}) "
            f"بواسطة {query.from_user.id}"
        )
    except Exception as e:
        logger.error(f"[control_panel] فشل set_active_theme({theme_id}): {e}", exc_info=True)
        return await query.answer("❌ فشل الحفظ في قاعدة البيانات.", show_alert=True)

    confirm_text = f"✅ تم تفعيل ثيم: <b>{name_ar}</b>"
    confirm_markup = types.InlineKeyboardMarkup([
        [types.InlineKeyboardButton("⬅️ رجوع لقائمة الأنماط", callback_data="panel:themes")],
    ])

    # محاولة تعديل الرسالة الحالية (قد تكون صورة من المعاينة)
    try:
        await query.edit_message_text(text=confirm_text, reply_markup=confirm_markup)
    except Exception:
        try:
            await query.message.delete()
        except Exception:
            pass
        await app.send_message(
            chat_id=query.message.chat.id,
            text=confirm_text,
            reply_markup=confirm_markup,
        )


# ==============================================================================
# قسم ➕ إنشاء ثيم مخصص (panel:themes:create) — الجزء الأول
# ==============================================================================
# نطاق هذا الجزء: إدخال نص القالب (message_template) وإعداد show_thumbnail
# فقط. تعديل button_layout (زر "▶️ التالي: تعديل الأزرار") سيُفعَّل في جزء
# قادم؛ حالياً يحفظ زر "💾 حفظ بدون تعديل الأزرار" الثيم مباشرة بنفس تخطيط
# أزرار الثيم الكلاسيكي (THEMES["classic"]["button_layout"]).
#
# ── الحالة المؤقتة (في الذاكرة فقط، لا تُخزَّن في DB قبل الحفظ النهائي) ──────
# _theme_create_state: dict[user_id: int, dict] حيث لكل user_id قاموس بالمفاتيح:
#   - "message_template": str | None  → نص القالب الذي أرسله المستخدم (None قبل استلامه)
#   - "show_thumbnail":   bool        → حالة تبديل الصورة المصغّرة (True افتراضياً)
#   - "prompt_msg":       types.Message → رسالة الشاشة الحالية (تُستخدَم لإعادة
#                                          تعديلها لاحقاً، حتى من المهمة الخلفية)
#   - "expires_at":       float        → time.time() + _THEME_CREATE_FLOW_TIMEOUT؛
#                                          الطابع الزمني الذي تنتهي عنده العملية كلها
#   - "watchdog_task":    asyncio.Task → مهمة خلفية تنفّذ التنظيف التلقائي عند
#                                          انتهاء المهلة الكلية (انظر _theme_create_watchdog)
#
# مفتاح القاموس هو user_id فقط (بدون chat_id)، وبذلك:
#   • كل مالك/سدو له حالة إنشاء مستقلة تماماً عن غيره (لا تداخل بين مالكَين).
#   • فتح "➕ إنشاء ثيم مخصص جديد" من جديد لنفس المستخدم يستبدل حالته السابقة
#     (تنظيف فوري عبر _cleanup_theme_create_state في بداية _handle_themes_create_start).
#
# ── آليتا المهلة (timeout) ───────────────────────────────────────────────────
#   1) مهلة إدخال نص القالب (دقيقتان = _INPUT_TIMEOUT الثابت المعرَّف أعلى
#      الملف): تُطبَّق فقط على استدعاء app.listen() الذي ينتظر رسالة القالب،
#      وهي مُقيَّدة أيضاً بألا تتجاوز الوقت المتبقي من المهلة الكلية (10 دقائق)
#      حتى لا تتجاوز مهلة الانتظار الفردية عمر الجلسة نفسه.
#   2) المهلة الكلية لعملية الإنشاء (10 دقائق = _THEME_CREATE_FLOW_TIMEOUT):
#      تُطبَّق على العملية بأكملها (إدخال القالب + تبديل الصورة + الانتقال بين
#      الأزرار)، وتُنفَّذ بطريقتين متكاملتين:
#        a) تنظيف "كسول" (lazy): كل معالج زر (toggle_thumb / next_buttons /
#           save_default) يستدعي _theme_create_get_live_state() أولاً، والتي
#           تتحقق من expires_at وتُنظِّف الحالة فوراً إذا انتهت المهلة قبل
#           تنفيذ أي إجراء.
#        b) مهمة خلفية فعّالة (watchdog): asyncio.Task واحدة لكل جلسة، تُنشَأ
#           في _handle_themes_create_start وتنام حتى expires_at، ثم -إن كانت
#           الجلسة نفسها لا تزال قائمة- تُنظِّف الحالة وتُحدِّث رسالة الشاشة
#           تلقائياً لإعلام المستخدم، حتى لو لم يضغط أي زر إطلاقاً. تُلغى هذه
#           المهمة تلقائياً عبر _cleanup_theme_create_state() عند أي إنهاء
#           طبيعي للعملية (حفظ ناجح أو إلغاء) لتفادي تحديث رسالة بعد انتهاء
#           الجلسة بشكل طبيعي.
# ==============================================================================

_theme_create_state: dict[int, dict] = {}

# مهلة العملية الكلية لإنشاء ثيم مخصص (ثانية) — 10 دقائق
_THEME_CREATE_FLOW_TIMEOUT = 600

# ==============================================================================
# حالة تعديل الأزرار (panel:themes:buttons) — مستقلة عن _theme_create_state
# المفتاح: user_id | القيم: {"button_layout": list[list[str]]}
# ==============================================================================

# الأزرار المتاحة — مستخرجة مباشرة من helpers/_inline.py → Inline.controls()
_AVAILABLE_BUTTONS: list[tuple[str, str]] = [
    ("▷",       "resume"),
    ("II",      "pause"),
    ("↻",       "replay"),
    ("‣‣I",     "skip"),
    ("▢",       "stop"),
    ("ᴅᴇʟᴇᴛᴇ", "close"),
]

# حالة تعديل الأزرار: user_id → {"button_layout": list[list[str]]}
_pending_themes: dict[int, dict] = {}


def _cleanup_theme_create_state(user_id: int) -> None:
    """يحذف الحالة المؤقتة لمستخدم معيّن، ويُلغي مهمة المراقبة (watchdog)
    المرتبطة بها إن كانت لا تزال قائمة (لا تأثير إن كانت قد انتهت فعلاً)."""
    state = _theme_create_state.pop(user_id, None)
    if state:
        task = state.get("watchdog_task")
        if task and not task.done():
            task.cancel()


def _theme_create_get_live_state(user_id: int) -> dict | None:
    """يُرجع الحالة المؤقتة لمستخدم إن كانت موجودة ولم تنتهِ مهلتها الكلية
    بعد؛ وإلا يُنظِّفها فوراً (تنظيف كسول) ويُرجع None."""
    state = _theme_create_state.get(user_id)
    if not state:
        return None
    if time.time() >= state["expires_at"]:
        _cleanup_theme_create_state(user_id)
        return None
    return state


async def _theme_create_watchdog(user_id: int, expires_at: float) -> None:
    """مهمة خلفية واحدة لكل جلسة إنشاء ثيم: تنتظر حتى نهاية المهلة الكلية
    (_THEME_CREATE_FLOW_TIMEOUT)، ثم -فقط إن كانت هذه الجلسة بعينها (تحقُّقاً
    عبر تطابق expires_at، لتفادي إنهاء جلسة جديدة أعاد المستخدم بدأها بنفس
    user_id) لا تزال قائمة- تُنظِّف الحالة وتُحدِّث رسالة الشاشة لإعلام
    المستخدم بانتهاء المهلة دون أي تدخّل يدوي منه.

    تُلغى هذه المهمة تلقائياً (فتخرج عبر asyncio.CancelledError) عند انتهاء
    العملية بشكل طبيعي قبل ذلك (حفظ أو إلغاء)، عبر _cleanup_theme_create_state().
    """
    delay = expires_at - time.time()
    if delay > 0:
        try:
            await asyncio.sleep(delay)
        except asyncio.CancelledError:
            return

    state = _theme_create_state.get(user_id)
    if not state or state.get("expires_at") != expires_at:
        # لم تعد هذه الجلسة قائمة، أو استُبدلت بجلسة أحدث لنفس المستخدم
        return

    _theme_create_state.pop(user_id, None)

    prompt_msg = state.get("prompt_msg")
    if prompt_msg:
        try:
            await prompt_msg.edit_text(
                "⏰ <b>انتهت المهلة الكلية لإنشاء الثيم (10 دقائق).</b>\n"
                "لم يُحفَظ أي ثيم، وتم تنظيف البيانات المؤقتة.",
                reply_markup=types.InlineKeyboardMarkup([
                    [types.InlineKeyboardButton(
                        "⬅️ رجوع لقائمة الأنماط", callback_data="panel:themes",
                    )],
                ]),
            )
        except Exception as e:
            logger.warning(
                f"[control_panel] تعذّر تحديث رسالة انتهاء المهلة الكلية "
                f"لإنشاء الثيم (user={user_id}): {e}"
            )


def _theme_create_step2_render(state: dict) -> tuple[str, types.InlineKeyboardMarkup]:
    """يبني نص ولوحة مفاتيح شاشة 'الخطوة 2' (بعد استلام القالب): تبديل
    الصورة المصغّرة، التالي (لاحقاً)، أو الحفظ المباشر بتخطيط classic."""
    show_thumb = state.get("show_thumbnail", True)
    tmpl = state.get("message_template", "") or ""
    text = (
        "✅ <b>تم استلام نص القالب بنجاح.</b>\n\n"
        "<b>القالب المخزَّن مؤقتاً (لم يُحفَظ بعد):</b>\n"
        f"<code>{html.escape(tmpl)}</code>\n\n"
        f"🖼️ الصورة المصغّرة لهذا الثيم: "
        f"<b>{'مفعّلة 🟢' if show_thumb else 'معطّلة 🔴'}</b>\n\n"
        "اختر الخطوة التالية:"
    )
    markup = types.InlineKeyboardMarkup([
        [types.InlineKeyboardButton(
            "🖼️ تفعيل/تعطيل الصورة المصغّرة",
            callback_data="panel:themes:create:toggle_thumb",
        )],
        [types.InlineKeyboardButton(
            "▶️ التالي: تعديل الأزرار",
            callback_data="panel:themes:create:next_buttons",
        )],
        [types.InlineKeyboardButton(
            "💾 حفظ بدون تعديل الأزرار",
            callback_data="panel:themes:create:save_default",
        )],
        [types.InlineKeyboardButton("❌ إلغاء", callback_data="panel:themes:create:cancel")],
    ])
    return text, markup


async def _handle_themes_create_start(query: types.CallbackQuery) -> None:
    """الجزء الأول من إنشاء ثيم مخصص: شرح المتغيرات المتاحة في القالب، ثم
    انتظار نص القالب الجديد من المستخدم كرد، ثم تخزينه مؤقتاً وعرض شاشة
    تبديل الصورة المصغّرة + أزرار التالي/الحفظ."""
    user_id = query.from_user.id

    # تنظيف أي جلسة إنشاء سابقة عالقة لهذا المستخدم (بدء نظيف من جديد)
    _cleanup_theme_create_state(user_id)

    # القالب الكلاسيكي مطابق حرفياً لـ locales/ar.json["play_media"]
    # (انظر التوثيق في رأس helpers/_themes.py) — يُستخدَم هنا كمثال مرجعي فقط.
    from UltraMusic.helpers._themes import THEMES as _TH  # noqa: PLC0415
    classic_template = _TH["classic"]["message_template"]

    explain_text = (
        "➕ <b>إنشاء ثيم مخصص جديد — الخطوة 1: نص القالب</b>\n\n"
        "🧩 <b>المتغيرات المتاحة في قالب الثيم:</b>\n"
        "• <code>{0}</code> → رابط المقطع (url)\n"
        "• <code>{1}</code> → عنوان المقطع (title)\n"
        "• <code>{2}</code> → المدة (duration)\n"
        "• <code>{3}</code> → مُرسل طلب التشغيل (user)\n\n"
        "⚠️ لا يوجد حقل باسم \"اسم الطالب\"؛ استخدم <code>{3}</code> فقط لهذا "
        "الغرض (وهو الحقل الوحيد المتاح فعلاً على الميديا لهذه الغاية).\n"
        "✅ يمكن استخدام وسوم HTML المدعومة في تيليجرام، مثل "
        "<code>&lt;b&gt;</code>، <code>&lt;i&gt;</code>، "
        "<code>&lt;a href=...&gt;</code>، <code>&lt;blockquote&gt;</code>.\n\n"
        "<b>مثال (نص الثيم الكلاسيكي الحالي):</b>\n"
        f"<code>{html.escape(classic_template)}</code>\n\n"
        "✍️ أرسل الآن نص القالب الجديد <b>كرد</b> في هذه المحادثة "
        "(أو /إلغاء للتراجع).\n"
        "⏳ مهلة إدخال النص: <b>دقيقتان</b>.\n"
        "⏳ المهلة الكلية لإنشاء الثيم بالكامل: <b>10 دقائق</b>."
    )

    await query.answer()
    prompt_msg = await query.edit_message_text(
        text=explain_text,
        reply_markup=types.InlineKeyboardMarkup([
            [types.InlineKeyboardButton("❌ إلغاء", callback_data="panel:themes:create:cancel")],
        ]),
    )

    expires_at = time.time() + _THEME_CREATE_FLOW_TIMEOUT
    state = {
        "message_template": None,
        "show_thumbnail": True,  # افتراضي مطابق لسلوك الثيم الكلاسيكي
        "prompt_msg": prompt_msg,
        "expires_at": expires_at,
        "watchdog_task": None,
    }
    _theme_create_state[user_id] = state
    state["watchdog_task"] = asyncio.create_task(
        _theme_create_watchdog(user_id, expires_at)
    )

    # مهلة انتظار نص القالب: دقيقتان، لكن لا تتجاوز الوقت المتبقي من
    # المهلة الكلية (10 دقائق) — لتفادي انتظار أطول من عمر الجلسة نفسها.
    remaining = expires_at - time.time()
    listen_timeout = max(1, min(_INPUT_TIMEOUT, int(remaining)))

    try:
        reply = await app.listen(
            chat_id=prompt_msg.chat.id,
            filters=filters.user(user_id) & filters.text,
            timeout=listen_timeout,
        )
    except asyncio.TimeoutError:
        # تحقّق أن الجلسة لم تُنظَّف بالفعل (مثلاً بواسطة watchdog إن كانت
        # المهلة الكلية قد انتهت فعلياً في نفس اللحظة تقريباً).
        if user_id in _theme_create_state and _theme_create_state[user_id] is state:
            _cleanup_theme_create_state(user_id)
            try:
                await prompt_msg.edit_text(
                    "⏰ انتهت مهلة إدخال القالب (دقيقتان). لم يُحفَظ أي تغيير.",
                    reply_markup=types.InlineKeyboardMarkup([
                        [types.InlineKeyboardButton(
                            "➕ حاول مجدداً", callback_data="panel:themes:create",
                        )],
                        [types.InlineKeyboardButton(
                            "⬅️ رجوع", callback_data="panel:themes",
                        )],
                    ]),
                )
            except Exception:
                pass
        return

    # احتياط: قد تكون الجلسة انتهت/استُبدلت أثناء الانتظار (سباق نادر مع watchdog)
    if user_id not in _theme_create_state or _theme_create_state[user_id] is not state:
        try:
            await reply.delete()
        except Exception:
            pass
        return

    try:
        await reply.delete()
    except Exception:
        pass

    new_template = (reply.text or "").strip()

    if new_template in ("/إلغاء", "إلغاء", "/cancel", "cancel"):
        _cleanup_theme_create_state(user_id)
        text = await _build_themes_text()
        kbd = await _themes_keyboard()
        try:
            await prompt_msg.edit_text(text=text, reply_markup=kbd)
        except Exception as e:
            logger.warning(f"[control_panel] فشل الرجوع لقائمة الأنماط بعد إلغاء الإنشاء: {e}")
        return

    if not new_template:
        _cleanup_theme_create_state(user_id)
        await prompt_msg.edit_text(
            "❌ النص فارغ. لم يُحفَظ أي تغيير.",
            reply_markup=types.InlineKeyboardMarkup([
                [types.InlineKeyboardButton("➕ حاول مجدداً", callback_data="panel:themes:create")],
                [types.InlineKeyboardButton("⬅️ رجوع", callback_data="panel:themes")],
            ]),
        )
        return

    # تحقّق أن القالب صالح فعلاً للاستخدام مع
    # .format(url, title, duration, user) — بنفس الترتيب المستخدم في
    # core/calls.py و build_now_playing_text، لتفادي حفظ ثيم سيتعطّل عند
    # أول استخدام فعلي له.
    try:
        new_template.format("https://t.me", "عنوان تجريبي", "3:45", "@تجريبي")
    except Exception as fmt_err:
        _cleanup_theme_create_state(user_id)
        await prompt_msg.edit_text(
            "❌ <b>القالب غير صالح:</b>\n"
            f"<code>{html.escape(str(fmt_err))}</code>\n\n"
            "تأكد من استخدام <code>{0}</code> <code>{1}</code> <code>{2}</code> "
            "<code>{3}</code> فقط كمتغيرات (وأن أي قوس مفرد آخر مُضاعَف "
            "<code>{{ }}</code>). لم يُحفَظ أي تغيير.",
            reply_markup=types.InlineKeyboardMarkup([
                [types.InlineKeyboardButton("➕ حاول مجدداً", callback_data="panel:themes:create")],
                [types.InlineKeyboardButton("⬅️ رجوع", callback_data="panel:themes")],
            ]),
        )
        return

    # ── تخزين مؤقت بالذاكرة فقط (لا DB بعد) ──────────────────────────────────
    state["message_template"] = new_template

    text, markup = _theme_create_step2_render(state)
    try:
        await prompt_msg.edit_text(text=text, reply_markup=markup)
    except Exception as e:
        logger.warning(
            f"[control_panel] فشل تحديث شاشة إنشاء الثيم بعد استلام القالب: {e}"
        )


async def _handle_themes_create_toggle_thumb(query: types.CallbackQuery) -> None:
    """يبدّل show_thumbnail في الحالة المؤقتة لهذا المستخدم ويعيد رسم الشاشة."""
    user_id = query.from_user.id
    state = _theme_create_get_live_state(user_id)
    if not state or not state.get("message_template"):
        await query.answer("⏰ انتهت الجلسة أو لم يُستلَم القالب بعد. ابدأ من جديد.", show_alert=True)
        try:
            await query.edit_message_text(
                text=await _build_themes_text(), reply_markup=await _themes_keyboard(),
            )
        except Exception:
            pass
        return

    state["show_thumbnail"] = not state.get("show_thumbnail", True)
    state_text = "مفعّلة 🟢" if state["show_thumbnail"] else "معطّلة 🔴"
    await query.answer(f"🖼️ الصورة المصغّرة: {state_text}")

    text, markup = _theme_create_step2_render(state)
    try:
        await query.edit_message_text(text=text, reply_markup=markup)
    except Exception as e:
        logger.warning(f"[control_panel] فشل تحديث شاشة إنشاء الثيم بعد toggle_thumb: {e}")


async def _handle_themes_create_next_buttons(query: types.CallbackQuery) -> None:
    """ينتقل إلى شاشة ✏️ تعديل الأزرار ويُهيّئ _pending_themes[user_id]
    مُبدَّأً من تخطيط أزرار الثيم الكلاسيكي."""
    user_id = query.from_user.id
    state = _theme_create_get_live_state(user_id)
    if not state:
        await query.answer("⏰ انتهت الجلسة. ابدأ من جديد.", show_alert=True)
        try:
            await query.edit_message_text(
                text=await _build_themes_text(), reply_markup=await _themes_keyboard(),
            )
        except Exception:
            pass
        return
    # هيّئ _pending_themes لهذا المستخدم من تخطيط classic
    # مع نقل القالب وإعداد الصورة من _theme_create_state
    _init_pending_buttons(
        user_id,
        template=state.get("message_template") or "",
        show_thumbnail=state.get("show_thumbnail", True),
    )
    layout = _pending_themes[user_id]["button_layout"]
    await query.answer()
    try:
        await query.edit_message_text(
            text=_build_buttons_screen_text(layout),
            reply_markup=_build_buttons_screen_markup(user_id),
        )
    except Exception:
        pass


async def _handle_themes_create_save_default(query: types.CallbackQuery) -> None:
    """يحفظ الثيم المخصص الجديد في DB مباشرة، بتخطيط أزرار classic الافتراضي،
    دون المرور بخطوة تعديل الأزرار."""
    user_id = query.from_user.id
    state = _theme_create_get_live_state(user_id)
    if not state or not state.get("message_template"):
        await query.answer("⏰ انتهت الجلسة أو لم يُستلَم القالب بعد. ابدأ من جديد.", show_alert=True)
        try:
            await query.edit_message_text(
                text=await _build_themes_text(), reply_markup=await _themes_keyboard(),
            )
        except Exception:
            pass
        return

    from UltraMusic.helpers._themes import THEMES as _TH  # noqa: PLC0415
    # نسخة مستقلة من تخطيط أزرار classic (تفادياً لمشاركة المرجع نفسه مع THEMES)
    classic_layout = [list(row) for row in _TH["classic"]["button_layout"]]

    theme_id = f"custom_{user_id}_{int(time.time())}"
    try:
        existing_count = len(await db.list_custom_themes())
    except Exception as e:
        logger.warning(f"[control_panel] تعذّر قراءة عدد الثيمات المخصصة الحالية: {e}")
        existing_count = 0
    name_ar = f"ثيم مخصص #{existing_count + 1}"

    theme_data = {
        "name_ar": name_ar,
        "message_template": state["message_template"],
        "button_layout": classic_layout,
        "show_thumbnail": state.get("show_thumbnail", True),
        "emoji_set": [],
    }

    try:
        await db.save_custom_theme(theme_id, theme_data)
        logger.info(
            f"[control_panel] ثيم مخصص جديد '{theme_id}' ({name_ar}) "
            f"بواسطة {user_id} (button_layout=classic، show_thumbnail="
            f"{theme_data['show_thumbnail']})"
        )
    except Exception as e:
        logger.error(f"[control_panel] فشل حفظ الثيم المخصص '{theme_id}': {e}", exc_info=True)
        return await query.answer("❌ فشل الحفظ في قاعدة البيانات.", show_alert=True)

    # الحفظ تم بنجاح: انهِ الجلسة المؤقتة (تُلغي أيضاً مهمة المراقبة الخلفية)
    _cleanup_theme_create_state(user_id)
    await query.answer("✅ تم حفظ الثيم.")

    confirm_text = (
        f"✅ <b>تم حفظ الثيم المخصص:</b> {name_ar}\n\n"
        "📐 تخطيط الأزرار: نفس تخطيط الثيم الكلاسيكي (الافتراضي) — يمكن "
        "تخصيصه لاحقاً عند تفعيل خطوة تعديل الأزرار.\n"
        f"🖼️ الصورة المصغّرة: "
        f"{'مفعّلة 🟢' if theme_data['show_thumbnail'] else 'معطّلة 🔴'}"
    )
    confirm_markup = types.InlineKeyboardMarkup([
        [types.InlineKeyboardButton(
            "👁️ معاينة الثيم", callback_data=f"panel:themes:preview:{theme_id}",
        )],
        [types.InlineKeyboardButton("⬅️ رجوع لقائمة الأنماط", callback_data="panel:themes")],
    ])
    try:
        await query.edit_message_text(text=confirm_text, reply_markup=confirm_markup)
    except Exception:
        try:
            await query.message.delete()
        except Exception:
            pass
        await app.send_message(
            chat_id=query.message.chat.id, text=confirm_text, reply_markup=confirm_markup,
        )


async def _handle_themes_create_cancel(query: types.CallbackQuery) -> None:
    """يُلغي عملية إنشاء الثيم الحالية وينظّف الحالة المؤقتة، ويرجع لقائمة الأنماط."""
    user_id = query.from_user.id
    _cleanup_theme_create_state(user_id)
    await query.answer("↩️ تم الإلغاء.")

    text = await _build_themes_text()
    kbd = await _themes_keyboard()
    try:
        await query.edit_message_text(text=text, reply_markup=kbd)
    except Exception:
        try:
            await query.message.delete()
        except Exception:
            pass
        await app.send_message(chat_id=query.message.chat.id, text=text, reply_markup=kbd)


# ==============================================================================
# قسم ✏️ تعديل الأزرار (panel:themes:buttons) — الجزء الأول: عرض، إضافة، حذف
# ==============================================================================


def _init_pending_buttons(
    user_id: int,
    template: str = "",
    show_thumbnail: bool = True,
) -> None:
    """ينشئ/يُعيد ضبط حالة تعديل الأزرار لمستخدم معيّن.

    يُهيِّئ button_layout من تخطيط الثيم الكلاسيكي، ويحفظ القالب
    وإعداد الصورة المصغّرة القادمَين من _theme_create_state.
    """
    from UltraMusic.helpers._themes import THEMES as _TH  # noqa: PLC0415
    classic_layout = [list(row) for row in _TH["classic"]["button_layout"]]
    _pending_themes[user_id] = {
        "button_layout": classic_layout,
        "template": template,
        "show_thumbnail": show_thumbnail,
    }


def _get_pending_layout(user_id: int) -> "list[list[str]] | None":
    """يُرجع button_layout الحالي أو None إن لم توجد جلسة."""
    entry = _pending_themes.get(user_id)
    return entry["button_layout"] if entry else None


def _build_buttons_screen_text(layout: "list[list[str]]") -> str:
    """يبني نص شاشة 'تعديل الأزرار' مع قائمة مرقمة بجميع الأزرار."""
    lines = ["✏️ <b>تعديل الأزرار</b>\n\n<b>الأزرار الحالية:</b>"]
    n = 1
    for row in layout:
        for btn in row:
            lines.append(f"{n}. <code>{btn}</code>")
            n += 1
    return "\n".join(lines)


def _build_buttons_screen_markup(user_id: int) -> types.InlineKeyboardMarkup:
    """يبني لوحة مفاتيح شاشة تعديل الأزرار الرئيسية."""
    return types.InlineKeyboardMarkup([
        [types.InlineKeyboardButton("➕ أضف زر",       callback_data=f"panel:themes:buttons:add:{user_id}")],
        [types.InlineKeyboardButton("🗑️ احذف زر",     callback_data=f"panel:themes:buttons:del:{user_id}")],
        [types.InlineKeyboardButton("🔃 أعد الترتيب",  callback_data=f"panel:themes:buttons:move:{user_id}")],
        [types.InlineKeyboardButton("💾 حفظ كثيم",    callback_data=f"panel:themes:buttons:save:{user_id}")],
        [types.InlineKeyboardButton("⬅️ رجوع",         callback_data="panel:themes:create")],
    ])


async def _handle_themes_buttons_main(
    query: types.CallbackQuery, user_id: int
) -> None:
    """يعرض شاشة 'تعديل الأزرار' الرئيسية."""
    layout = _get_pending_layout(user_id)
    if layout is None:
        await query.answer("⚠️ انتهت الجلسة. ابدأ من جديد.", show_alert=True)
        try:
            await query.edit_message_text(
                text=await _build_themes_text(), reply_markup=await _themes_keyboard()
            )
        except Exception:
            pass
        return
    await query.answer()
    try:
        await query.edit_message_text(
            text=_build_buttons_screen_text(layout),
            reply_markup=_build_buttons_screen_markup(user_id),
        )
    except Exception:
        pass


async def _handle_themes_buttons_add(
    query: types.CallbackQuery, user_id: int
) -> None:
    """يعرض شاشة اختيار زر للإضافة — كل زر متاح كزر inline مستقل."""
    layout = _get_pending_layout(user_id)
    if layout is None:
        await query.answer("⚠️ انتهت الجلسة. ابدأ من جديد.", show_alert=True)
        try:
            await query.edit_message_text(
                text=await _build_themes_text(), reply_markup=await _themes_keyboard()
            )
        except Exception:
            pass
        return
    await query.answer()
    rows = [
        [types.InlineKeyboardButton(
            text=symbol,
            callback_data=f"panel:themes:buttons:add:{user_id}:{idx}",
        )]
        for idx, (symbol, _) in enumerate(_AVAILABLE_BUTTONS)
    ]
    rows.append([types.InlineKeyboardButton(
        "⬅️ رجوع", callback_data=f"panel:themes:buttons:{user_id}"
    )])
    try:
        await query.edit_message_text(
            text="➕ <b>اختر زراً لإضافته:</b>\n\nسيُضاف في نهاية الصف الأول.",
            reply_markup=types.InlineKeyboardMarkup(rows),
        )
    except Exception:
        pass


async def _handle_themes_buttons_add_select(
    query: types.CallbackQuery, user_id: int, btn_idx: int
) -> None:
    """يُضيف الزر المختار لنهاية الصف الأول ويعود تلقائياً للشاشة الرئيسية."""
    layout = _get_pending_layout(user_id)
    if layout is None:
        await query.answer("⚠️ انتهت الجلسة. ابدأ من جديد.", show_alert=True)
        try:
            await query.edit_message_text(
                text=await _build_themes_text(), reply_markup=await _themes_keyboard()
            )
        except Exception:
            pass
        return
    if btn_idx < 0 or btn_idx >= len(_AVAILABLE_BUTTONS):
        await query.answer("❌ زر غير صالح.", show_alert=True)
        return
    symbol = _AVAILABLE_BUTTONS[btn_idx][0]
    # أضف لنهاية الصف الأول؛ أنشئ الصف إن كان layout فارغاً
    if not layout:
        layout.append([symbol])
    else:
        layout[0].append(symbol)
    await query.answer(f"✅ تمت إضافة {symbol}")
    # ارجع تلقائياً لشاشة تعديل الأزرار
    try:
        await query.edit_message_text(
            text=_build_buttons_screen_text(layout),
            reply_markup=_build_buttons_screen_markup(user_id),
        )
    except Exception:
        pass


async def _handle_themes_buttons_del(
    query: types.CallbackQuery, user_id: int
) -> None:
    """يعرض شاشة حذف زر — الأزرار الحالية كأزرار inline (رقم + رمز)."""
    layout = _get_pending_layout(user_id)
    if layout is None:
        await query.answer("⚠️ انتهت الجلسة. ابدأ من جديد.", show_alert=True)
        try:
            await query.edit_message_text(
                text=await _build_themes_text(), reply_markup=await _themes_keyboard()
            )
        except Exception:
            pass
        return
    await query.answer()
    rows = []
    flat_idx = 0
    for row in layout:
        for btn in row:
            rows.append([types.InlineKeyboardButton(
                text=f"{flat_idx + 1}. {btn}",
                callback_data=f"panel:themes:buttons:del:{user_id}:{flat_idx}",
            )])
            flat_idx += 1
    rows.append([types.InlineKeyboardButton(
        "⬅️ رجوع", callback_data=f"panel:themes:buttons:{user_id}"
    )])
    try:
        await query.edit_message_text(
            text="🗑️ <b>اختر الزر الذي تريد حذفه:</b>",
            reply_markup=types.InlineKeyboardMarkup(rows),
        )
    except Exception:
        pass


async def _handle_themes_buttons_del_select(
    query: types.CallbackQuery, user_id: int, flat_idx: int
) -> None:
    """يحذف الزر بالفهرس المسطح ويعود تلقائياً للشاشة الرئيسية."""
    layout = _get_pending_layout(user_id)
    if layout is None:
        await query.answer("⚠️ انتهت الجلسة. ابدأ من جديد.", show_alert=True)
        try:
            await query.edit_message_text(
                text=await _build_themes_text(), reply_markup=await _themes_keyboard()
            )
        except Exception:
            pass
        return
    # حماية: لا تحذف إن بقي زر واحد فقط
    total = sum(len(row) for row in layout)
    if total <= 1:
        await query.answer("⚠️ يجب أن يبقى زر واحد على الأقل.", show_alert=True)
        return
    # ابحث عن الموضع بالفهرس المسطح
    counter = 0
    deleted = False
    for ri, row in enumerate(layout):
        for ci in range(len(row)):
            if counter == flat_idx:
                row.pop(ci)
                deleted = True
                break
            counter += 1
        if deleted:
            # إن أصبح الصف فارغاً احذفه
            if not layout[ri]:
                layout.pop(ri)
            break
    if not deleted:
        await query.answer("❌ الزر غير موجود.", show_alert=True)
        return
    await query.answer("✅ تم الحذف")
    # ارجع تلقائياً لشاشة تعديل الأزرار
    try:
        await query.edit_message_text(
            text=_build_buttons_screen_text(layout),
            reply_markup=_build_buttons_screen_markup(user_id),
        )
    except Exception:
        pass


# ==============================================================================
# شاشة 🔃 إعادة الترتيب (panel:themes:buttons:move:<user_id>)
# ==============================================================================


def _flat_to_rowcol(
    layout: "list[list[str]]", flat_idx: int
) -> "tuple[int, int] | tuple[None, None]":
    """يحوّل فهرساً مسطحاً إلى (row_idx, col_idx) ضمن layout.
    يُرجع (None, None) إن كان الفهرس خارج النطاق.
    """
    counter = 0
    for ri, row in enumerate(layout):
        for ci in range(len(row)):
            if counter == flat_idx:
                return ri, ci
            counter += 1
    return None, None


def _build_move_screen_text(layout: "list[list[str]]") -> str:
    """يبني نص شاشة 'إعادة الترتيب' مع قائمة مرقمة بجميع الأزرار."""
    lines = ["🔃 <b>إعادة الترتيب</b>\n\n<b>الأزرار الحالية:</b>"]
    n = 1
    for row in layout:
        for btn in row:
            lines.append(f"{n}. <code>{btn}</code>")
            n += 1
    lines.append("\nاختر زراً لتحريكه يساراً ⬅️ أو يميناً ➡️ ضمن نفس الصف.")
    return "\n".join(lines)


def _build_move_screen_markup(
    user_id: int, layout: "list[list[str]]"
) -> types.InlineKeyboardMarkup:
    """يبني لوحة مفاتيح شاشة إعادة الترتيب.

    لكل زر: ⬅️  <label>  ➡️  في صف مستقل.
    # TODO: النقل بين الصفوف — غير مُنفَّذ حالياً.
    """
    rows = []
    flat_idx = 0
    for row in layout:
        for btn in row:
            rows.append([
                types.InlineKeyboardButton(
                    "⬅️",
                    callback_data=f"panel:themes:buttons:moveleft:{user_id}:{flat_idx}",
                ),
                types.InlineKeyboardButton(
                    f"{flat_idx + 1}. {btn}",
                    callback_data=f"panel:themes:buttons:{user_id}",  # no-op label
                ),
                types.InlineKeyboardButton(
                    "➡️",
                    callback_data=f"panel:themes:buttons:moveright:{user_id}:{flat_idx}",
                ),
            ])
            flat_idx += 1
    rows.append([types.InlineKeyboardButton(
        "⬅️ رجوع", callback_data=f"panel:themes:buttons:{user_id}"
    )])
    return types.InlineKeyboardMarkup(rows)


async def _handle_themes_buttons_move(
    query: types.CallbackQuery, user_id: int
) -> None:
    """يعرض شاشة إعادة ترتيب الأزرار."""
    layout = _get_pending_layout(user_id)
    if layout is None:
        await query.answer("⚠️ انتهت الجلسة. ابدأ من جديد.", show_alert=True)
        try:
            await query.edit_message_text(
                text=await _build_themes_text(), reply_markup=await _themes_keyboard()
            )
        except Exception:
            pass
        return
    await query.answer()
    try:
        await query.edit_message_text(
            text=_build_move_screen_text(layout),
            reply_markup=_build_move_screen_markup(user_id, layout),
        )
    except Exception:
        pass


async def _handle_themes_buttons_move_dir(
    query: types.CallbackQuery, user_id: int, flat_idx: int, direction: str
) -> None:
    """يحرّك الزر يساراً أو يميناً ضمن نفس الصف ويُحدّث الشاشة فوراً.

    direction: "left" | "right"
    # TODO: النقل بين الصفوف — غير مُنفَّذ حالياً.
    """
    layout = _get_pending_layout(user_id)
    if layout is None:
        await query.answer("⚠️ انتهت الجلسة. ابدأ من جديد.", show_alert=True)
        try:
            await query.edit_message_text(
                text=await _build_themes_text(), reply_markup=await _themes_keyboard()
            )
        except Exception:
            pass
        return

    ri, ci = _flat_to_rowcol(layout, flat_idx)
    if ri is None:
        await query.answer("❌ الزر غير موجود.", show_alert=True)
        return

    row = layout[ri]
    if direction == "left":
        if ci == 0:
            await query.answer()   # أقصى اليسار — لا تفعل شيئاً
            return
        row[ci], row[ci - 1] = row[ci - 1], row[ci]
    else:  # right
        if ci >= len(row) - 1:
            await query.answer()   # أقصى اليمين — لا تفعل شيئاً
            return
        row[ci], row[ci + 1] = row[ci + 1], row[ci]

    await query.answer("✅")
    try:
        await query.edit_message_text(
            text=_build_move_screen_text(layout),
            reply_markup=_build_move_screen_markup(user_id, layout),
        )
    except Exception:
        pass


# ==============================================================================
# شاشة 💾 حفظ كثيم (panel:themes:buttons:save:<user_id>)
# ==============================================================================


async def _handle_themes_buttons_save(
    query: types.CallbackQuery, user_id: int
) -> None:
    """يطلب اسم الثيم من المستخدم ثم يحفظه في DB ويفتح شاشة المعاينة."""
    entry = _pending_themes.get(user_id)
    if entry is None:
        await query.answer("⚠️ انتهت الجلسة. ابدأ من جديد.", show_alert=True)
        try:
            await query.edit_message_text(
                text=await _build_themes_text(), reply_markup=await _themes_keyboard()
            )
        except Exception:
            pass
        return

    await query.answer()
    prompt_msg = await query.edit_message_text(
        text=(
            "💾 <b>حفظ الثيم الجديد</b>\n\n"
            "✍️ أرسل <b>اسماً</b> للثيم الجديد كرد في هذه المحادثة.\n"
            "⏳ المهلة: <b>دقيقتان</b>."
        ),
        reply_markup=types.InlineKeyboardMarkup([
            [types.InlineKeyboardButton(
                "❌ إلغاء", callback_data=f"panel:themes:buttons:{user_id}"
            )],
        ]),
    )

    # انتظر ردّ المستخدم
    try:
        reply = await app.listen(
            chat_id=query.message.chat.id,
            filters=filters.user(user_id) & filters.text,
            timeout=_INPUT_TIMEOUT,
        )
    except asyncio.TimeoutError:
        try:
            await prompt_msg.edit_text(
                "⏰ انتهت المهلة. لم يُحفَظ أي تغيير.",
                reply_markup=types.InlineKeyboardMarkup([
                    [types.InlineKeyboardButton(
                        "⬅️ رجوع", callback_data=f"panel:themes:buttons:{user_id}"
                    )],
                ]),
            )
        except Exception:
            pass
        return

    # احذف رسالة المستخدم
    try:
        await reply.delete()
    except Exception:
        pass

    name = reply.text.strip()

    # دعم أمر الإلغاء
    if name in ("/إلغاء", "إلغاء", "/cancel", "cancel"):
        try:
            layout_now = _get_pending_layout(user_id)
            await prompt_msg.edit_text(
                "↩️ تم الإلغاء.",
                reply_markup=_build_buttons_screen_markup(user_id)
                if layout_now is not None
                else types.InlineKeyboardMarkup([
                    [types.InlineKeyboardButton("⬅️ رجوع", callback_data="panel:themes")]
                ]),
            )
        except Exception:
            pass
        return

    if not name:
        try:
            await prompt_msg.edit_text(
                "❌ الاسم لا يمكن أن يكون فارغاً. لم يُحفَظ أي تغيير.",
                reply_markup=types.InlineKeyboardMarkup([
                    [types.InlineKeyboardButton(
                        "⬅️ رجوع", callback_data=f"panel:themes:buttons:{user_id}"
                    )],
                ]),
            )
        except Exception:
            pass
        return

    # أعِد قراءة entry (قد يكون المستخدم غيّر الجلسة أثناء الانتظار)
    entry = _pending_themes.get(user_id)
    if entry is None:
        try:
            await prompt_msg.edit_text(
                "⚠️ انتهت الجلسة أثناء الانتظار. ابدأ من جديد.",
                reply_markup=types.InlineKeyboardMarkup([
                    [types.InlineKeyboardButton("⬅️ رجوع", callback_data="panel:themes")]
                ]),
            )
        except Exception:
            pass
        return

    # بناء theme_id و data
    theme_id = name.lower().strip().replace(" ", "_")
    theme_data = {
        "id": theme_id,
        "name_ar": name,
        "message_template": entry.get("template") or "",
        "button_layout": entry.get("button_layout", []),
        "show_thumbnail": entry.get("show_thumbnail", True),
        "emoji_set": [],
    }

    # حفظ في DB
    try:
        await db.save_custom_theme(theme_id, theme_data)
        logger.info(
            f"[control_panel] ثيم مخصص محرَّر '{theme_id}' ({name}) "
            f"بواسطة {user_id} — button_layout={theme_data['button_layout']}"
        )
    except Exception as e:
        logger.error(f"[control_panel] فشل حفظ الثيم المخصص '{theme_id}': {e}", exc_info=True)
        try:
            await prompt_msg.edit_text(
                "❌ فشل الحفظ في قاعدة البيانات.",
                reply_markup=types.InlineKeyboardMarkup([
                    [types.InlineKeyboardButton(
                        "⬅️ رجوع", callback_data=f"panel:themes:buttons:{user_id}"
                    )],
                ]),
            )
        except Exception:
            pass
        return

    # احذف من الذاكرة
    _pending_themes.pop(user_id, None)
    # نظّف أيضاً _theme_create_state إن كانت لا تزال قائمة
    _cleanup_theme_create_state(user_id)

    # افتح شاشة المعاينة فوراً
    await query.answer("✅ تم الحفظ!")
    # نستعمل edit_text على prompt_msg ثم نحاكي التنقل لشاشة preview
    confirm_markup = types.InlineKeyboardMarkup([
        [types.InlineKeyboardButton(
            "👁️ معاينة الثيم",
            callback_data=f"panel:themes:preview:{theme_id}",
        )],
        [types.InlineKeyboardButton("⬅️ رجوع لقائمة الأنماط", callback_data="panel:themes")],
    ])
    try:
        await prompt_msg.edit_text(
            text=f"✅ <b>تم حفظ الثيم:</b> {name}\n\n"
                 f"🔖 المعرّف: <code>{theme_id}</code>\n"
                 f"🖼️ الصورة المصغّرة: "
                 f"{'مفعّلة 🟢' if theme_data['show_thumbnail'] else 'معطّلة 🔴'}",
            reply_markup=confirm_markup,
        )
    except Exception:
        try:
            await query.message.delete()
        except Exception:
            pass
        await app.send_message(
            chat_id=query.message.chat.id,
            text=f"✅ <b>تم حفظ الثيم:</b> {name}",
            reply_markup=confirm_markup,
        )


async def _build_assistants_text() -> str:
    """بناء نص شاشة المساعدين مع عدد المجموعات الحالي لكل منهم."""
    from collections import Counter
    lines = ["🤖 <b>المساعدون النشطون</b>\n"]
    try:
        counts = Counter(db.assistant.values())
        for i, client in enumerate(userbot.clients, start=1):
            name = getattr(client, "name", f"Assistant{i}")
            uname = getattr(client, "username", None)
            uname_str = f"@{uname}" if uname else "—"
            chats = counts.get(i, 0)
            lines.append(
                f"<b>{i}.</b> {name} ({uname_str})\n"
                f"   📊 المجموعات المُعيَّنة: <b>{chats}</b>"
            )
    except Exception as e:
        logger.warning(f"[control_panel] تعذّر قراءة بيانات المساعدين: {e}")
        lines.append("⚠️ تعذّر تحميل البيانات.")
    total = len(db.assistant)
    lines.append(f"\n📋 إجمالي المجموعات المرصودة: <b>{total}</b>")
    return "\n".join(lines)


def _assistants_keyboard(confirm: bool = False) -> types.InlineKeyboardMarkup:
    """لوحة مفاتيح شاشة المساعدين."""
    if confirm:
        return types.InlineKeyboardMarkup([
            [
                types.InlineKeyboardButton("✅ تأكيد إعادة التوزيع",
                                           callback_data="panel:assistants:rebalance:yes"),
                types.InlineKeyboardButton("❌ إلغاء",
                                           callback_data="panel:assistants"),
            ],
        ])
    return types.InlineKeyboardMarkup([
        [types.InlineKeyboardButton("🔄 إعادة توزيع المجموعات (Rebalance)",
                                    callback_data="panel:assistants:rebalance")],
        _back_row("panel:main"),
    ])


async def _build_api_text() -> str:
    """بناء نص شاشة API والكوكيز."""
    lines = ["🔑 <b>مفاتيح API والكوكيز</b>\n"]

    # ── مفاتيح API ────────────────────────────────────────────────────────────
    try:
        stats = yt.get_api_stats()
        if stats:
            lines.append("<b>مفاتيح ArtistBots API:</b>")
            for s in stats:
                bar = "✅" if s["pct"] >= 80 else ("⚠️" if s["pct"] >= 40 else "❌")
                lines.append(
                    f"  {bar} <code>{s['masked']}</code> — "
                    f"نجاح {s['pct']}% ({s['ok']}✓/{s['fail']}✗) "
                    f"| آخر استخدام: {s['last_used']}"
                )
        else:
            keys_total = len(config.API_KEYS) if config.API_KEYS else 0
            lines.append(f"<b>المفاتيح المُعرَّفة:</b> {keys_total} (لم تُستخدم بعد)")
    except Exception as e:
        logger.warning(f"[control_panel] تعذّر قراءة إحصاء API: {e}")
        lines.append("⚠️ تعذّر تحميل إحصاء المفاتيح.")

    lines.append("")

    # ── الكوكيز ───────────────────────────────────────────────────────────────
    try:
        cookie_dir = "UltraMusic/cookies"
        cookie_files = [
            f for f in os.listdir(cookie_dir)
            if f.endswith(".txt") and not f.startswith("README")
        ]
        lines.append(f"<b>🍪 ملفات الكوكيز:</b> <b>{len(cookie_files)}</b> ملف محلي")
        if cookie_files:
            for cf in cookie_files[:5]:
                lines.append(f"  • <code>{cf}</code>")
            if len(cookie_files) > 5:
                lines.append(f"  … و{len(cookie_files) - 5} ملف آخر")
    except Exception as e:
        logger.warning(f"[control_panel] تعذّر قراءة مجلد الكوكيز: {e}")
        lines.append("⚠️ تعذّر قراءة مجلد الكوكيز.")

    return "\n".join(lines)


def _api_keyboard(confirm: bool = False) -> types.InlineKeyboardMarkup:
    """لوحة مفاتيح شاشة API والكوكيز."""
    if confirm:
        return types.InlineKeyboardMarkup([
            [
                types.InlineKeyboardButton("✅ تأكيد تحديث الكوكيز",
                                           callback_data="panel:api:cookies:yes"),
                types.InlineKeyboardButton("❌ إلغاء",
                                           callback_data="panel:api"),
            ],
        ])
    return types.InlineKeyboardMarkup([
        [types.InlineKeyboardButton("🍪 تحديث الكوكيز الآن",
                                    callback_data="panel:api:cookies")],
        _back_row("panel:main"),
    ])


def _set_prompt_text(key: str, current_display: str, unit: str) -> str:
    meta = _NUMERIC_SETTINGS[key]
    return (
        f"✏️ <b>تعديل: {meta['label']}</b>\n\n"
        f"القيمة الحالية: <b>{current_display} {unit}</b>\n\n"
        f"أرسل الرقم الجديد بالـ {unit} (أو /إلغاء للتراجع).\n"
        f"⏳ المهلة: دقيقتان."
    )


# ------------------------------------------------------------------------------
# أمر /لوحة_التحكم
# ------------------------------------------------------------------------------

@app.on_message(command(["لوحة_التحكم", "control_panel"]) & filters.private)
async def open_control_panel(_, m: types.Message):
    """يفتح لوحة التحكم — متاح للمالك والمستخدمين المتميزين فقط."""
    try:
        if m.from_user.id != app.owner and m.from_user.id not in app.sudoers:
            return await m.reply_text("🔐 هذا الأمر للمالك فقط.")

        await m.reply_text(text=MAIN_TEXT, reply_markup=_main_keyboard())

    except Exception as e:
        logger.error(f"[control_panel] خطأ في أمر /لوحة_التحكم: {e}", exc_info=True)


# ------------------------------------------------------------------------------
# معالج callback_query الموحّد
# ------------------------------------------------------------------------------

@app.on_callback_query(filters.regex(r"^panel:"))
async def panel_callback(_, query: types.CallbackQuery):
    """
    معالج موحّد لجميع callbacks لوحة التحكم.

    الأنماط:
        panel:main
        panel:settings
        panel:settings:set:<KEY>
        panel:settings:toggle:<KEY>
    """
    try:
        user_id = query.from_user.id
        if user_id != app.owner and user_id not in app.sudoers:
            return await query.answer("🔐 هذا الأمر للمالك فقط.", show_alert=True)

        data = query.data
        parts = data.split(":")  # ['panel', 'settings', 'set', 'DURATION_LIMIT']

        # ── panel:main ────────────────────────────────────────────────────────
        if data == "panel:main":
            await query.answer()
            await query.edit_message_text(text=MAIN_TEXT, reply_markup=_main_keyboard())

        # ── panel:settings ────────────────────────────────────────────────────
        elif data == "panel:settings":
            await query.answer()
            await query.edit_message_text(
                text=SETTINGS_TEXT,
                reply_markup=await _settings_keyboard(),
            )

        # ── panel:ui ──────────────────────────────────────────────────────────
        elif data == "panel:ui":
            await query.answer()
            await query.edit_message_text(
                text=UI_TEXT,
                reply_markup=await _ui_keyboard(),
            )

        # ── panel:ui:toggle:thumb_gen ─────────────────────────────────────────
        elif data == "panel:ui:toggle:thumb_gen":
            await _handle_ui_toggle_thumb(query)

        # ── panel:ui:set:default_thumb ────────────────────────────────────────
        elif data == "panel:ui:set:default_thumb":
            await _handle_set_default_thumb(query)

        # ── panel:ui:set:play_template ────────────────────────────────────────
        elif data == "panel:ui:set:play_template":
            await _handle_set_play_template(query)

        # ── panel:ui:toggle:button_layout ─────────────────────────────────────
        elif data == "panel:ui:toggle:button_layout":
            await _handle_ui_toggle_button_layout(query)

        # ── panel:themes ──────────────────────────────────────────────────────
        elif data == "panel:themes":
            await query.answer()
            text = await _build_themes_text()
            kbd  = await _themes_keyboard()
            # قد تكون الرسالة الحالية صورة (من شاشة المعاينة) → نحاول edit أولاً
            try:
                await query.edit_message_text(text=text, reply_markup=kbd)
            except Exception:
                try:
                    await query.message.delete()
                except Exception:
                    pass
                await app.send_message(
                    chat_id=query.message.chat.id,
                    text=text,
                    reply_markup=kbd,
                )

        # ── panel:themes:preview:<theme_id> ────────────────────────────────────
        elif len(parts) >= 4 and parts[1] == "themes" and parts[2] == "preview":
            theme_id_req = ":".join(parts[3:])  # دعم theme_id يحتوي على ":" إن وُجد
            await _handle_themes_preview(query, theme_id_req)

        # ── panel:themes:activate:<theme_id> ─────────────────────────────────
        elif len(parts) >= 4 and parts[1] == "themes" and parts[2] == "activate":
            theme_id_req = ":".join(parts[3:])
            await _handle_themes_activate(query, theme_id_req)

        # ── panel:themes:create — الجزء الأول (إدخال القالب + show_thumbnail) ──
        elif data == "panel:themes:create":
            await _handle_themes_create_start(query)

        elif data == "panel:themes:create:toggle_thumb":
            await _handle_themes_create_toggle_thumb(query)

        elif data == "panel:themes:create:next_buttons":
            await _handle_themes_create_next_buttons(query)

        elif data == "panel:themes:create:save_default":
            await _handle_themes_create_save_default(query)

        elif data == "panel:themes:create:cancel":
            await _handle_themes_create_cancel(query)

        # ── panel:themes:buttons — شاشة تعديل الأزرار (الجزء الأول) ───────────
        elif len(parts) >= 4 and parts[1] == "themes" and parts[2] == "buttons":
            sub = parts[3]  # قد يكون user_id رقماً أو اسم شاشة فرعية
            if sub.isdigit():
                # panel:themes:buttons:<user_id>  → الشاشة الرئيسية
                await _handle_themes_buttons_main(query, int(sub))
            elif sub == "add" and len(parts) >= 5:
                uid = int(parts[4])
                if len(parts) >= 6 and parts[5].isdigit():
                    # panel:themes:buttons:add:<user_id>:<btn_idx>
                    await _handle_themes_buttons_add_select(query, uid, int(parts[5]))
                else:
                    # panel:themes:buttons:add:<user_id>
                    await _handle_themes_buttons_add(query, uid)
            elif sub == "del" and len(parts) >= 5:
                uid = int(parts[4])
                if len(parts) >= 6 and parts[5].isdigit():
                    # panel:themes:buttons:del:<user_id>:<flat_idx>
                    await _handle_themes_buttons_del_select(query, uid, int(parts[5]))
                else:
                    # panel:themes:buttons:del:<user_id>
                    await _handle_themes_buttons_del(query, uid)
            elif sub == "move" and len(parts) >= 5:
                # panel:themes:buttons:move:<user_id>
                await _handle_themes_buttons_move(query, int(parts[4]))
            elif sub == "moveleft" and len(parts) >= 6:
                # panel:themes:buttons:moveleft:<user_id>:<flat_idx>
                await _handle_themes_buttons_move_dir(
                    query, int(parts[4]), int(parts[5]), "left"
                )
            elif sub == "moveright" and len(parts) >= 6:
                # panel:themes:buttons:moveright:<user_id>:<flat_idx>
                await _handle_themes_buttons_move_dir(
                    query, int(parts[4]), int(parts[5]), "right"
                )
            elif sub == "save" and len(parts) >= 5:
                # panel:themes:buttons:save:<user_id>
                await _handle_themes_buttons_save(query, int(parts[4]))

        # ── panel:language ────────────────────────────────────────────────────
        elif data == "panel:language":
            await query.answer()
            await query.edit_message_text(
                text=LANGUAGE_TEXT,
                reply_markup=types.InlineKeyboardMarkup([
                    _back_row("panel:main"),
                ]),
            )

        # ── panel:assistants ──────────────────────────────────────────────────
        elif data == "panel:assistants":
            await query.answer()
            text = await _build_assistants_text()
            await query.edit_message_text(
                text=text,
                reply_markup=_assistants_keyboard(),
            )

        elif data == "panel:assistants:rebalance":
            await query.answer()
            await query.edit_message_text(
                "⚠️ <b>تأكيد إعادة التوزيع</b>\n\n"
                "سيتم إعادة تعيين كل مجموعة لمساعد عشوائياً.\n"
                "هذا لن يوقف التشغيل الحالي.",
                reply_markup=_assistants_keyboard(confirm=True),
            )

        elif data == "panel:assistants:rebalance:yes":
            await _handle_rebalance(query)

        # ── panel:api ─────────────────────────────────────────────────────────
        elif data == "panel:api":
            await query.answer()
            text = await _build_api_text()
            await query.edit_message_text(
                text=text,
                reply_markup=_api_keyboard(),
            )

        elif data == "panel:api:cookies":
            await query.answer()
            await query.edit_message_text(
                "⚠️ <b>تأكيد تحديث الكوكيز</b>\n\n"
                "سيتم تنزيل ملفات الكوكيز من COOKIES_URL الآن.\n"
                "العملية قد تستغرق بضع ثوانٍ.",
                reply_markup=_api_keyboard(confirm=True),
            )

        elif data == "panel:api:cookies:yes":
            await _handle_cookies_refresh(query)

        # ── panel:settings:quality:audio / video (الجودة الافتراضية) ──────────
        elif data == "panel:settings:quality:audio":
            await _handle_quality_audio_show(query)

        elif data == "panel:settings:quality:video":
            await _handle_quality_video_show(query)

        elif len(parts) == 6 and parts[2] == "quality" and parts[3] == "audio" and parts[4] == "set":
            await _handle_quality_audio_set(query, parts[5])

        elif len(parts) == 6 and parts[2] == "quality" and parts[3] == "video" and parts[4] == "set":
            await _handle_quality_video_set(query, parts[5])

        # ── panel:settings:set:<KEY> ──────────────────────────────────────────
        elif len(parts) == 4 and parts[2] == "set":
            key = parts[3]
            if key not in _NUMERIC_SETTINGS:
                return await query.answer("❓ إعداد غير معروف.", show_alert=True)
            await _handle_set_numeric(query, key)

        # ── panel:settings:toggle:<KEY> ───────────────────────────────────────
        elif len(parts) == 4 and parts[2] == "toggle":
            key = parts[3]
            await _handle_toggle(query, key)

        # ── panel:system ──────────────────────────────────────────────────────
        elif data == "panel:system":
            await query.answer()
            text = await _build_system_text()
            await query.edit_message_text(
                text=text,
                reply_markup=await _system_keyboard(),
            )

        elif data == "panel:system:toggle:maintenance":
            await _handle_system_maintenance_toggle(query)

        elif data == "panel:system:restart":
            await _handle_system_restart(query)

        elif data == "panel:system:restart:yes":
            await _handle_system_restart_confirmed(query)

        elif data == "panel:system:cleanup":
            await _handle_system_cleanup(query)

        # ── panel:bans ────────────────────────────────────────────────────────
        elif data == "panel:bans":
            await query.answer()
            text = await _build_bans_text()
            await query.edit_message_text(text=text, reply_markup=await _bans_keyboard())

        elif data == "panel:bans:list":
            await _handle_bans_list(query)

        elif data == "panel:bans:add":
            await _handle_bans_add(query)

        elif data == "panel:bans:del":
            await _handle_bans_del(query)

        elif data == "panel:bans:excluded":
            await _handle_excluded_show(query)

        elif data == "panel:bans:excluded:add":
            await _handle_excluded_add(query)

        elif data == "panel:bans:excluded:del":
            await _handle_excluded_del(query)

        elif data == "panel:bans:sudoers":
            await _handle_sudoers_show(query)

        elif data == "panel:bans:sudoers:add":
            await _handle_sudoers_add(query)

        elif data == "panel:bans:sudoers:del":
            await _handle_sudoers_del(query)

        elif data == "panel:bans:blacklist":
            await _handle_blacklist_main_show(query)

        elif data == "panel:bans:blacklist:users":
            await _handle_blacklist_users_show(query)

        elif data == "panel:bans:blacklist:chats":
            await _handle_blacklist_chats_show(query)

        elif data == "panel:bans:blacklist:users:add":
            await _handle_blacklist_users_add(query)

        elif data == "panel:bans:blacklist:users:del":
            await _handle_blacklist_users_del(query)

        elif data == "panel:bans:blacklist:chats:add":
            await _handle_blacklist_chats_add(query)

        elif data == "panel:bans:blacklist:chats:del":
            await _handle_blacklist_chats_del(query)

        elif data == "panel:settings:cleanmode_info":
            await _handle_cleanmode_info(query)

        # ── panel:broadcast ──────────────────────────────────────────────────
        elif data == "panel:broadcast":
            await query.answer()
            await query.edit_message_text(
                text=BROADCAST_TEXT,
                reply_markup=_broadcast_keyboard(),
            )

        # ── panel:leave ───────────────────────────────────────────────────────
        elif data == "panel:leave":
            await query.answer()
            await query.edit_message_text(
                text=LEAVE_TEXT,
                reply_markup=_leave_keyboard(),
            )

        elif data == "panel:leave:specific":
            await _handle_leave_specific(query)

        elif data == "panel:leave:idle":
            await _handle_leave_idle_confirm(query)

        elif data == "panel:leave:idle:yes":
            await _handle_leave_idle_confirmed(query)

        else:
            await query.answer("⏳ قيد الإنشاء...", show_alert=False)

    except Exception as e:
        logger.error(
            f"[control_panel] خطأ في callback '{query.data}': {e}",
            exc_info=True,
        )
        try:
            await query.answer("❌ حدث خطأ، حاول مجدداً.", show_alert=True)
        except Exception:
            pass


# ------------------------------------------------------------------------------
# منطق تعديل القيمة الرقمية (انتظار رد المستخدم)
# ------------------------------------------------------------------------------

async def _handle_set_numeric(query: types.CallbackQuery, key: str) -> None:
    """اطلب من المستخدم إدخال قيمة جديدة للإعداد الرقمي."""
    meta = _NUMERIC_SETTINGS[key]
    current_raw = await _get_numeric_value(key)
    current_display = _display_numeric(key, current_raw)

    prompt_text = _set_prompt_text(key, current_display, meta["display_unit"])

    await query.answer()
    prompt_msg = await query.edit_message_text(
        text=prompt_text,
        reply_markup=types.InlineKeyboardMarkup([
            [types.InlineKeyboardButton("❌ إلغاء", callback_data="panel:settings")]
        ]),
    )

    # انتظر ردّ المستخدم في نفس المحادثة
    try:
        reply = await app.listen(
            chat_id=query.message.chat.id,
            filters=filters.user(query.from_user.id) & filters.text,
            timeout=_INPUT_TIMEOUT,
        )
    except asyncio.TimeoutError:
        try:
            await prompt_msg.edit_text(
                "⏰ انتهت المهلة. لم يُحفَظ أي تغيير.",
                reply_markup=types.InlineKeyboardMarkup([
                    [types.InlineKeyboardButton("⬅️ رجوع", callback_data="panel:settings")]
                ]),
            )
        except Exception:
            pass
        return

    # حذف رسالة المستخدم تلقائياً
    try:
        await reply.delete()
    except Exception:
        pass

    text = reply.text.strip()

    # دعم أمر الإلغاء
    if text in ("/إلغاء", "إلغاء", "/cancel", "cancel"):
        await prompt_msg.edit_text(
            "↩️ تم الإلغاء.",
            reply_markup=types.InlineKeyboardMarkup([
                [types.InlineKeyboardButton("⬅️ رجوع", callback_data="panel:settings")]
            ]),
        )
        return

    # التحقق من صحة الإدخال
    if not text.isdigit() or int(text) <= 0:
        await prompt_msg.edit_text(
            "❌ قيمة غير صالحة. يجب أن تكون رقماً صحيحاً موجباً.\n"
            "لم يُحفَظ أي تغيير.",
            reply_markup=types.InlineKeyboardMarkup([
                [types.InlineKeyboardButton("⬅️ رجوع", callback_data="panel:settings")]
            ]),
        )
        return

    user_input = int(text)

    # تحويل الدقائق → ثواني عند الحاجة
    if meta["stored_unit"] == "seconds":
        store_value = user_input * 60
    else:
        store_value = user_input

    try:
        await db.set_setting(key, store_value)
        logger.info(
            f"[control_panel] {key} عُدّل إلى {store_value} "
            f"(عرض: {user_input} {meta['display_unit']}) "
            f"بواسطة المستخدم {query.from_user.id}"
        )
    except Exception as e:
        logger.error(f"[control_panel] فشل حفظ {key}: {e}", exc_info=True)
        await prompt_msg.edit_text(
            "❌ فشل الحفظ في قاعدة البيانات. حاول مجدداً.",
            reply_markup=types.InlineKeyboardMarkup([
                [types.InlineKeyboardButton("⬅️ رجوع", callback_data="panel:settings")]
            ]),
        )
        return

    await prompt_msg.edit_text(
        f"✅ تم تحديث <b>{meta['label']}</b> إلى "
        f"<b>{user_input} {meta['display_unit']}</b>.",
        reply_markup=types.InlineKeyboardMarkup([
            [types.InlineKeyboardButton("⬅️ رجوع للإعدادات", callback_data="panel:settings")]
        ]),
    )


# ------------------------------------------------------------------------------
# منطق تبديل القيمة المنطقية (فوري)
# ------------------------------------------------------------------------------

async def _handle_toggle(query: types.CallbackQuery, key: str) -> None:
    """بدّل قيمة إعداد منطقي فوراً وأعِد رسم لوحة الإعدادات."""

    # ── سويتش الخلو من المستخدمين ──────────────────────────────────────────
    if key == "auto_leave_empty":
        # TODO: المنطق الفعلي سيُضاف في مرحلة لاحقة، هذا الزر يهيّئ القيمة في DB فقط حالياً
        try:
            current = await db.get_setting("auto_leave_empty_enabled", True)
        except Exception as e:
            logger.warning(f"[control_panel] تعذّر قراءة auto_leave_empty_enabled: {e}")
            current = True
        new_val = not current
        try:
            await db.set_setting("auto_leave_empty_enabled", new_val)
            logger.info(
                f"[control_panel] auto_leave_empty_enabled → {new_val} "
                f"بواسطة {query.from_user.id}"
            )
            # إن أُوقفت الميزة: ألغِ فوراً كل مهام auto-leave النشطة
            if not new_val:
                try:
                    _tune_instance.cancel_all_auto_leave_tasks()
                except Exception as _ce:
                    logger.debug(f"[control_panel] cancel_all_auto_leave_tasks: {_ce}")
        except Exception as e:
            logger.error(f"[control_panel] فشل حفظ auto_leave_empty_enabled: {e}", exc_info=True)
            return await query.answer("❌ فشل الحفظ.", show_alert=True)

        state_text = "مفعّلة 🟢" if new_val else "معطّلة 🔴"
        await query.answer(f"🚪 مغادرة عند الخلو: {state_text}", show_alert=False)
        await query.edit_message_reply_markup(reply_markup=await _settings_keyboard())
        return

    # ── VIDEO_PLAY — يستخدم db.set_vplay_enabled الموجودة ───────────────────
    if key == "VIDEO_PLAY":
        try:
            current = await db.get_vplay_enabled()
            new_val = not current
            await db.set_vplay_enabled(new_val)
            logger.info(
                f"[control_panel] VIDEO_PLAY → {new_val} "
                f"بواسطة {query.from_user.id}"
            )
        except Exception as e:
            logger.error(f"[control_panel] فشل تبديل VIDEO_PLAY: {e}", exc_info=True)
            return await query.answer("❌ فشل الحفظ.", show_alert=True)

        state_text = "مفعّل 🟢" if new_val else "معطّل 🔴"
        await query.answer(f"🎬 تشغيل الفيديو: {state_text}", show_alert=False)
        await query.edit_message_reply_markup(reply_markup=await _settings_keyboard())
        return

    # ── AUTO_END و AUTO_LEAVE — عبر db.get_setting / db.set_setting ──────────
    if key in _TOGGLE_SETTINGS:
        meta = _TOGGLE_SETTINGS[key]
        cfg_val = getattr(config, meta["config_attr"])
        try:
            current = await db.get_setting(key, cfg_val)
            new_val = not current
            await db.set_setting(key, new_val)
            logger.info(
                f"[control_panel] {key} → {new_val} "
                f"بواسطة {query.from_user.id}"
            )
        except Exception as e:
            logger.error(f"[control_panel] فشل تبديل {key}: {e}", exc_info=True)
            return await query.answer("❌ فشل الحفظ.", show_alert=True)

        state_text = "مفعّل 🟢" if new_val else "معطّل 🔴"
        await query.answer(f"{meta['label']}: {state_text}", show_alert=False)
        await query.edit_message_reply_markup(reply_markup=await _settings_keyboard())
        return

    await query.answer("❓ مفتاح غير معروف.", show_alert=True)


# ==============================================================================
# قسم 🎨 واجهة التشغيل — دوال المعالجة
# ==============================================================================

async def _handle_ui_toggle_thumb(query: types.CallbackQuery) -> None:
    """بدّل قيمة THUMB_GEN فوراً وأعِد رسم شاشة واجهة التشغيل."""
    try:
        current = await db.get_setting("thumb_gen", config.THUMB_GEN)
    except Exception as e:
        logger.warning(f"[control_panel] تعذّر قراءة thumb_gen: {e}")
        current = config.THUMB_GEN

    new_val = not current
    try:
        await db.set_setting("thumb_gen", new_val)
        logger.info(
            f"[control_panel] thumb_gen → {new_val} "
            f"بواسطة {query.from_user.id}"
        )
    except Exception as e:
        logger.error(f"[control_panel] فشل حفظ thumb_gen: {e}", exc_info=True)
        return await query.answer("❌ فشل الحفظ.", show_alert=True)

    state_text = "مفعّل 🟢" if new_val else "معطّل 🔴"
    await query.answer(f"🖼 الصورة المصغّرة: {state_text}", show_alert=False)
    await query.edit_message_reply_markup(reply_markup=await _ui_keyboard())


async def _handle_set_default_thumb(query: types.CallbackQuery) -> None:
    """اطلب من المالك إرسال صورة كرد ثم خزّن file_id في DB."""
    await query.answer()
    prompt_msg = await query.edit_message_text(
        "🖼 <b>تغيير الصورة الافتراضية</b>\n\n"
        "أرسل الصورة الجديدة كرد في هذه المحادثة.\n"
        "⏳ المهلة: دقيقتان.",
        reply_markup=types.InlineKeyboardMarkup([
            [types.InlineKeyboardButton("❌ إلغاء", callback_data="panel:ui")]
        ]),
    )

    try:
        reply = await app.listen(
            chat_id=query.message.chat.id,
            filters=filters.user(query.from_user.id) & filters.photo,
            timeout=_INPUT_TIMEOUT,
        )
    except asyncio.TimeoutError:
        try:
            await prompt_msg.edit_text(
                "⏰ انتهت المهلة. لم يُحفَظ أي تغيير.",
                reply_markup=types.InlineKeyboardMarkup([
                    [types.InlineKeyboardButton("⬅️ رجوع", callback_data="panel:ui")]
                ]),
            )
        except Exception:
            pass
        return

    # حذف رسالة الصورة المُرسَلة تلقائياً
    try:
        await reply.delete()
    except Exception:
        pass

    file_id = reply.photo.file_id
    try:
        await db.set_setting("default_thumb_file_id", file_id)
        logger.info(
            f"[control_panel] default_thumb_file_id مُحدَّث "
            f"بواسطة {query.from_user.id}"
        )
    except Exception as e:
        logger.error(f"[control_panel] فشل حفظ default_thumb_file_id: {e}", exc_info=True)
        await prompt_msg.edit_text(
            "❌ فشل الحفظ في قاعدة البيانات. حاول مجدداً.",
            reply_markup=types.InlineKeyboardMarkup([
                [types.InlineKeyboardButton("⬅️ رجوع", callback_data="panel:ui")]
            ]),
        )
        return

    await prompt_msg.edit_text(
        "✅ تم حفظ الصورة الافتراضية الجديدة.",
        reply_markup=types.InlineKeyboardMarkup([
            [types.InlineKeyboardButton("⬅️ رجوع لواجهة التشغيل", callback_data="panel:ui")]
        ]),
    )


async def _handle_set_play_template(query: types.CallbackQuery) -> None:
    """اعرض القالب الحالي واطلب نصاً جديداً، ثم خزّنه في DB."""
    # قراءة القالب الحالي: DB أولاً ثم ar.json كاحتياطي
    try:
        current_tmpl = await db.get_setting("play_media_template", None)
    except Exception as e:
        logger.warning(f"[control_panel] تعذّر قراءة play_media_template: {e}")
        current_tmpl = None

    if not current_tmpl:
        # القيمة الافتراضية من ar.json
        try:
            from UltraMusic.core.lang import _DEFAULT_LANG  # noqa: PLC0415
            current_tmpl = _DEFAULT_LANG.get("play_media", "")
        except Exception:
            current_tmpl = ""

    await query.answer()
    prompt_msg = await query.edit_message_text(
        "✏️ <b>تعديل قالب رسالة التشغيل</b>\n\n"
        "<b>القالب الحالي:</b>\n"
        f"<code>{current_tmpl}</code>\n\n"
        "أرسل النص الجديد كرد (أو /إلغاء للتراجع).\n"
        "📌 المتغيرات: <code>{0}</code>=رابط، <code>{1}</code>=عنوان، "
        "<code>{2}</code>=مدة، <code>{3}</code>=مستخدم.\n"
        "⏳ المهلة: دقيقتان.",
        reply_markup=types.InlineKeyboardMarkup([
            [types.InlineKeyboardButton("❌ إلغاء", callback_data="panel:ui")]
        ]),
    )

    try:
        reply = await app.listen(
            chat_id=query.message.chat.id,
            filters=filters.user(query.from_user.id) & filters.text,
            timeout=_INPUT_TIMEOUT,
        )
    except asyncio.TimeoutError:
        try:
            await prompt_msg.edit_text(
                "⏰ انتهت المهلة. لم يُحفَظ أي تغيير.",
                reply_markup=types.InlineKeyboardMarkup([
                    [types.InlineKeyboardButton("⬅️ رجوع", callback_data="panel:ui")]
                ]),
            )
        except Exception:
            pass
        return

    try:
        await reply.delete()
    except Exception:
        pass

    new_text = reply.text.strip()

    if new_text in ("/إلغاء", "إلغاء", "/cancel", "cancel"):
        await prompt_msg.edit_text(
            "↩️ تم الإلغاء.",
            reply_markup=types.InlineKeyboardMarkup([
                [types.InlineKeyboardButton("⬅️ رجوع", callback_data="panel:ui")]
            ]),
        )
        return

    if not new_text:
        await prompt_msg.edit_text(
            "❌ النص فارغ. لم يُحفَظ أي تغيير.",
            reply_markup=types.InlineKeyboardMarkup([
                [types.InlineKeyboardButton("⬅️ رجوع", callback_data="panel:ui")]
            ]),
        )
        return

    try:
        await db.set_setting("play_media_template", new_text)
        logger.info(
            f"[control_panel] play_media_template مُحدَّث "
            f"بواسطة {query.from_user.id}"
        )
    except Exception as e:
        logger.error(f"[control_panel] فشل حفظ play_media_template: {e}", exc_info=True)
        await prompt_msg.edit_text(
            "❌ فشل الحفظ في قاعدة البيانات. حاول مجدداً.",
            reply_markup=types.InlineKeyboardMarkup([
                [types.InlineKeyboardButton("⬅️ رجوع", callback_data="panel:ui")]
            ]),
        )
        return

    await prompt_msg.edit_text(
        "✅ تم حفظ قالب رسالة التشغيل الجديد.",
        reply_markup=types.InlineKeyboardMarkup([
            [types.InlineKeyboardButton("⬅️ رجوع لواجهة التشغيل", callback_data="panel:ui")]
        ]),
    )


async def _handle_ui_toggle_button_layout(query: types.CallbackQuery) -> None:
    """بدّل تخطيط أزرار التحكم بين compact وexpanded فوراً."""
    try:
        current = await db.get_setting("button_layout", "compact")
        if current not in ("compact", "expanded"):
            current = "compact"
    except Exception as e:
        logger.warning(f"[control_panel] تعذّر قراءة button_layout: {e}")
        current = "compact"

    new_val = "expanded" if current == "compact" else "compact"
    try:
        await db.set_setting("button_layout", new_val)
        logger.info(
            f"[control_panel] button_layout → {new_val} "
            f"بواسطة {query.from_user.id}"
        )
    except Exception as e:
        logger.error(f"[control_panel] فشل حفظ button_layout: {e}", exc_info=True)
        return await query.answer("❌ فشل الحفظ.", show_alert=True)

    label = "مضغوط — صف واحد 🔳" if new_val == "compact" else "موسّع — صفّان 🔲"
    await query.answer(f"📐 تخطيط الأزرار: {label}", show_alert=False)
    await query.edit_message_reply_markup(reply_markup=await _ui_keyboard())


# ==============================================================================
# قسم 🤖 المساعدون — دوال التنفيذ
# ==============================================================================

async def _handle_rebalance(query: types.CallbackQuery) -> None:
    """أعِد توزيع جميع المجموعات على المساعدين عشوائياً."""
    await query.answer("⏳ جارٍ إعادة التوزيع...", show_alert=False)

    try:
        chat_ids = list(db.assistant.keys())
        if not chat_ids:
            await query.edit_message_text(
                "ℹ️ لا توجد مجموعات مُعيَّنة حالياً.",
                reply_markup=_assistants_keyboard(),
            )
            return

        count = 0
        errors = 0
        for chat_id in chat_ids:
            try:
                await db.set_assistant(chat_id)
                count += 1
            except Exception as e:
                logger.warning(f"[control_panel] rebalance: فشل تعيين {chat_id}: {e}")
                errors += 1

        logger.info(
            f"[control_panel] Rebalance: {count} مجموعة أُعيد توزيعها"
            f"{', ' + str(errors) + ' أخطاء' if errors else ''}. "
            f"بواسطة {query.from_user.id}"
        )

        result_text = await _build_assistants_text()
        result_text += (
            f"\n\n✅ تمت إعادة التوزيع: <b>{count}</b> مجموعة"
        )
        if errors:
            result_text += f" (<b>{errors}</b> أخطاء)"

        await query.edit_message_text(
            text=result_text,
            reply_markup=_assistants_keyboard(),
        )
    except Exception as e:
        logger.error(f"[control_panel] خطأ في rebalance: {e}", exc_info=True)
        await query.edit_message_text(
            "❌ حدث خطأ أثناء إعادة التوزيع.",
            reply_markup=_assistants_keyboard(),
        )


# ==============================================================================
# قسم 🔑 مفاتيح API والكوكيز — دوال التنفيذ
# ==============================================================================

async def _handle_cookies_refresh(query: types.CallbackQuery) -> None:
    """نزّل الكوكيز الآن من COOKIES_URL واعرض النتيجة."""
    await query.answer("⏳ جارٍ تحديث الكوكيز...", show_alert=False)

    if not config.COOKIES_URL:
        await query.edit_message_text(
            "⚠️ لم يُعرَّف COOKIES_URL في الإعدادات.",
            reply_markup=_api_keyboard(),
        )
        return

    try:
        await yt.save_cookies(config.COOKIES_URL)
        logger.info(
            f"[control_panel] تحديث الكوكيز: نجح "
            f"بواسطة {query.from_user.id}"
        )
        # أعِد بناء نص الشاشة لعرض العدد الجديد
        text = await _build_api_text()
        text += "\n\n✅ <b>تم تحديث الكوكيز بنجاح.</b>"
        await query.edit_message_text(
            text=text,
            reply_markup=_api_keyboard(),
        )
    except Exception as e:
        logger.error(f"[control_panel] فشل تحديث الكوكيز: {e}", exc_info=True)
        await query.edit_message_text(
            f"❌ فشل تحديث الكوكيز:\n<code>{str(e)[:200]}</code>",
            reply_markup=_api_keyboard(),
        )


# ==============================================================================
# قسم 🛠️ الصيانة والنظام (panel:system)
# ==============================================================================


def _fmt_uptime(seconds: float) -> str:
    """تنسيق مدة التشغيل: أيام وساعات ودقائق."""
    s = int(seconds)
    d, s = divmod(s, 86400)
    h, s = divmod(s, 3600)
    m, s = divmod(s, 60)
    parts = []
    if d:
        parts.append(f"{d}ي")
    if h:
        parts.append(f"{h}س")
    if m or not parts:
        parts.append(f"{m}د")
    return " ".join(parts)


def _get_downloads_size_sync() -> tuple[float, int]:
    """
    احسب حجم downloads/ ومكونه عدد الملفات (sync — يُستدعى عبر asyncio.to_thread).
    يعيد (size_mb, file_count).
    """
    dl_dir = Path("downloads")
    if not dl_dir.exists():
        return 0.0, 0
    total = count = 0
    for f in dl_dir.iterdir():
        if f.is_file():
            try:
                total += f.stat().st_size
                count += 1
            except OSError:
                pass
    return round(total / (1024 * 1024), 2), count


async def _build_system_text() -> str:
    """بناء نص شاشة الصيانة والنظام مع الإحصائيات الحالية."""
    lines = ["🛠️ <b>الصيانة والنظام</b>\n"]

    # ── وضع الصيانة ────────────────────────────────────────────────────────
    try:
        maint = await db.get_maintenance()
    except Exception as e:
        logger.warning(f"[system] تعذّر قراءة get_maintenance: {e}")
        maint = False
    maint_icon = "🔴 صيانة" if maint else "🟢 يعمل"
    lines.append(f"<b>وضع الصيانة:</b> {maint_icon}")

    # ── إحصائيات ──────────────────────────────────────────────────────────
    try:
        chats_count = len(await db.get_chats())
    except Exception:
        chats_count = len(db.chats)

    try:
        users_count = len(await db.get_users())
    except Exception:
        users_count = len(db.users)

    active_calls = len(db.active_calls)
    uptime_sec = time.time() - boot
    uptime_str = _fmt_uptime(uptime_sec)

    try:
        size_mb, file_count = await asyncio.to_thread(_get_downloads_size_sync)
    except Exception:
        size_mb, file_count = 0.0, 0

    lines.append(f"<b>📊 المجموعات:</b> {chats_count}")
    lines.append(f"<b>👤 المستخدمون:</b> {users_count}")
    lines.append(f"<b>📞 المكالمات النشطة:</b> {active_calls}")
    lines.append(f"<b>⏱ مدة التشغيل:</b> {uptime_str}")
    lines.append(f"<b>💾 downloads/:</b> {size_mb} MB ({file_count} ملف)")

    return "\n".join(lines)


async def _system_keyboard() -> types.InlineKeyboardMarkup:
    """لوحة مفاتيح شاشة الصيانة والنظام."""
    try:
        maint = await db.get_maintenance()
    except Exception:
        maint = False
    maint_label = "🔴 وضع الصيانة: مفعّل — اضغط للإيقاف" if maint else "🟢 وضع الصيانة: يعمل — اضغط للتفعيل"

    return types.InlineKeyboardMarkup([
        [types.InlineKeyboardButton(maint_label,
                                    callback_data="panel:system:toggle:maintenance")],
        [types.InlineKeyboardButton("🔁 إعادة تشغيل البوت",
                                    callback_data="panel:system:restart")],
        [types.InlineKeyboardButton("🧹 تنظيف downloads/ الآن",
                                    callback_data="panel:system:cleanup")],
        _back_row("panel:main"),
    ])


async def _handle_system_maintenance_toggle(query: types.CallbackQuery) -> None:
    """بدّل وضع الصيانة فوراً وأعِد رسم الشاشة."""
    try:
        current = await db.get_maintenance()
        new_val = not current
        await db.set_maintenance(new_val)
        logger.info(
            f"[system] وضع الصيانة → {new_val} بواسطة {query.from_user.id}"
        )
    except Exception as e:
        logger.error(f"[system] فشل تبديل الصيانة: {e}", exc_info=True)
        return await query.answer("❌ فشل الحفظ.", show_alert=True)

    state_text = "مفعّل 🔴" if new_val else "معطّل 🟢"
    await query.answer(f"🔧 وضع الصيانة: {state_text}", show_alert=False)
    text = await _build_system_text()
    await query.edit_message_text(text=text, reply_markup=await _system_keyboard())


async def _handle_system_restart(query: types.CallbackQuery) -> None:
    """اعرض تأكيد إعادة التشغيل."""
    await query.answer()
    await query.edit_message_text(
        "⚠️ <b>تأكيد إعادة التشغيل</b>\n\n"
        "سيُعاد تشغيل البوت الآن وقد تنقطع التشغيلات الجارية.",
        reply_markup=types.InlineKeyboardMarkup([
            [
                types.InlineKeyboardButton("✅ تأكيد",
                                           callback_data="panel:system:restart:yes"),
                types.InlineKeyboardButton("❌ إلغاء",
                                           callback_data="panel:system"),
            ],
        ]),
    )


async def _handle_system_restart_confirmed(query: types.CallbackQuery) -> None:
    """نفّذ إعادة التشغيل — يستدعي _do_restart من restart.py مباشرةً."""
    import sys as _sys  # noqa: PLC0415
    _restart_mod = _sys.modules.get("UltraMusic.plugins.admin-controls.restart")
    _do_restart = getattr(_restart_mod, "_do_restart", None) if _restart_mod else None
    if _do_restart is None:
        # fallback نادر: وحدة restart لم تُحمَّل بعد
        import importlib as _il  # noqa: PLC0415
        _restart_mod = _il.import_module("UltraMusic.plugins.admin-controls.restart")
        _do_restart = _restart_mod._do_restart
    await query.answer("♻️ جارٍ إعادة التشغيل...", show_alert=False)
    try:
        await query.edit_message_text(
            "🔄 <b>جارٍ إعادة التشغيل...</b>\n\nسيعود البوت خلال لحظات."
        )
    except Exception:
        pass
    await _do_restart()


def _cleanup_downloads_now_sync(max_age_hours: int = 48) -> tuple[int, float]:
    """
    احذف ملفات downloads/ الأقدم من max_age_hours.
    يعيد (deleted_count, freed_mb).  — sync لاستخدامه مع asyncio.to_thread.
    """
    dl_dir = Path("downloads")
    if not dl_dir.exists():
        return 0, 0.0
    cutoff = time.time() - max_age_hours * 3600
    deleted = freed = 0
    for f in dl_dir.iterdir():
        if not f.is_file():
            continue
        if f.suffix.lower() in (".part", ".ytdl", ".temp"):
            continue
        try:
            st = f.stat()
            if st.st_mtime < cutoff:
                freed += st.st_size
                f.unlink()
                deleted += 1
        except OSError:
            pass
    return deleted, round(freed / (1024 * 1024), 2)


async def _handle_system_cleanup(query: types.CallbackQuery) -> None:
    """نظّف downloads/ الآن واعرض النتيجة."""
    await query.answer("🧹 جارٍ التنظيف...", show_alert=False)
    try:
        # إذا كانت آلية التنظيف الدورية تصدّر دالة sweep نستدعيها مباشرةً
        # وإلا نستخدم دالتنا المحلية (48 ساعة)
        deleted, freed_mb = await asyncio.to_thread(_cleanup_downloads_now_sync, 48)
        logger.info(
            f"[system] تنظيف downloads: {deleted} ملف، {freed_mb} MB مُستردَّة"
            f" — بواسطة {query.from_user.id}"
        )
    except Exception as e:
        logger.error(f"[system] خطأ في تنظيف downloads: {e}", exc_info=True)
        await query.answer("❌ فشل التنظيف.", show_alert=True)
        return

    text = await _build_system_text()
    text += (
        f"\n\n✅ <b>اكتمل التنظيف:</b> حُذف <b>{deleted}</b> ملف"
        f" واسترُدَّ <b>{freed_mb} MB</b>."
    )
    await query.edit_message_text(text=text, reply_markup=await _system_keyboard())


# ==============================================================================
# قسم 🚫 الحظر والصلاحيات (panel:bans)
# ==============================================================================

_BANS_INPUT_TIMEOUT = 120   # ثانية — مهلة انتظار إدخال آيدي
_EXCLUDED_PAGE_SIZE = 20    # حد العرض في صفحة واحدة


# ── دوال مساعدة ────────────────────────────────────────────────────────────

async def _get_gbanned() -> list[int]:
    """اقرأ قائمة المحظورين مع حماية من الأخطاء."""
    try:
        return list(await db.get_gbanned())
    except Exception as e:
        logger.warning(f"[bans] تعذّر قراءة gbanned: {e}")
        return []


async def _get_excluded_chats() -> list[int]:
    """
    اقرأ EXCLUDED_CHATS: DB أولاً، ثم config كاحتياطي.
    القائمة مخزّنة في DB كـ list[int] تحت مفتاح 'excluded_chats'.
    """
    try:
        stored = await db.get_setting("excluded_chats", None)
        if stored is not None:
            return list(stored)
    except Exception as e:
        logger.warning(f"[bans] تعذّر قراءة excluded_chats من DB: {e}")
    return list(config.EXCLUDED_CHATS)


async def _save_excluded_chats(chats: list[int]) -> None:
    """
    احفظ EXCLUDED_CHATS في DB وحدّث config.EXCLUDED_CHATS في الذاكرة فوراً
    حتى تلتقطها autoleave/cleanmode دون إعادة تشغيل.
    """
    await db.set_setting("excluded_chats", chats)
    config.EXCLUDED_CHATS = chats          # تحديث فوري في الذاكرة


async def _get_sudoers_safe() -> list[int]:
    """اقرأ قائمة السودورز مع حماية من الأخطاء."""
    try:
        return list(await db.get_sudoers())
    except Exception as e:
        logger.warning(f"[bans] تعذّر قراءة sudoers: {e}")
        return list(app.sudoers)


async def _get_bl_users_safe() -> list[int]:
    """اقرأ قائمة المستخدمين المحظورين من البوت (Blacklist) مع حماية من الأخطاء."""
    try:
        return list(await db.get_blacklisted(chat=False))
    except Exception as e:
        logger.warning(f"[bans] تعذّر قراءة bl_users: {e}")
        return []


async def _get_bl_chats_safe() -> list[int]:
    """اقرأ قائمة المجموعات المحظورة من البوت (Blacklist) مع حماية من الأخطاء."""
    try:
        return list(await db.get_blacklisted(chat=True))
    except Exception as e:
        logger.warning(f"[bans] تعذّر قراءة bl_chats: {e}")
        return []


# ── نصوص وأدوات العرض ──────────────────────────────────────────────────────

async def _build_bans_text() -> str:
    """نص الشاشة الرئيسية لقسم الحظر."""
    gbanned = await _get_gbanned()
    excluded = await _get_excluded_chats()
    sudoers = await _get_sudoers_safe()
    bl_users = await _get_bl_users_safe()
    bl_chats = await _get_bl_chats_safe()
    lines = [
        "🚫 <b>الحظر والصلاحيات</b>\n",
        f"<b>المحظورون عالمياً (Global Ban):</b> <b>{len(gbanned)}</b> مستخدم",
        f"<b>المجموعات المستثناة (EXCLUDED_CHATS):</b> <b>{len(excluded)}</b> مجموعة",
        f"<b>السودورز (Sudo Users):</b> <b>{len(sudoers)}</b> مستخدم",
        f"<b>القائمة السوداء (Blacklist):</b> {len(bl_users)} مستخدم / {len(bl_chats)} مجموعة",
    ]
    return "\n".join(lines)


async def _bans_keyboard() -> types.InlineKeyboardMarkup:
    """لوحة مفاتيح شاشة الحظر الرئيسية."""
    gbanned = await _get_gbanned()
    excluded = await _get_excluded_chats()
    sudoers = await _get_sudoers_safe()
    return types.InlineKeyboardMarkup([
        [types.InlineKeyboardButton(
            f"🔍 عرض قائمة المحظورين ({len(gbanned)})",
            callback_data="panel:bans:list")],
        [
            types.InlineKeyboardButton("🚫 حظر مستخدم",  callback_data="panel:bans:add"),
            types.InlineKeyboardButton("✅ رفع حظر",     callback_data="panel:bans:del"),
        ],
        [types.InlineKeyboardButton(
            f"🏘 المجموعات المستثناة ({len(excluded)})",
            callback_data="panel:bans:excluded")],
        [types.InlineKeyboardButton(
            f"👑 السودورز ({len(sudoers)})",
            callback_data="panel:bans:sudoers")],
        [types.InlineKeyboardButton(
            "🛑 القائمة السوداء (Blacklist)",
            callback_data="panel:bans:blacklist")],
        _back_row("panel:main"),
    ])


# ── عرض قائمة المحظورين ────────────────────────────────────────────────────

async def _handle_bans_list(query: types.CallbackQuery) -> None:
    """اعرض أول 20 آيدي محظور مع ملاحظة العدد الكلي."""
    await query.answer()
    gbanned = await _get_gbanned()
    total = len(gbanned)

    if not total:
        text = "🚫 <b>قائمة الحظر العالمي</b>\n\nلا يوجد أي مستخدم محظور حالياً."
    else:
        preview = gbanned[:20]
        lines = ["🚫 <b>قائمة الحظر العالمي</b>\n",
                 f"إجمالي المحظورين: <b>{total}</b> مستخدم\n"]
        for i, uid in enumerate(preview, 1):
            lines.append(f"  {i}. <code>{uid}</code>")
        if total > 20:
            lines.append(f"\n… و<b>{total - 20}</b> مستخدم آخر غير معروض.")
        text = "\n".join(lines)

    await query.edit_message_text(
        text=text,
        reply_markup=types.InlineKeyboardMarkup([
            [types.InlineKeyboardButton("⬅️ رجوع", callback_data="panel:bans")],
        ]),
    )


# ── حظر مستخدم ─────────────────────────────────────────────────────────────

async def _handle_bans_add(query: types.CallbackQuery) -> None:
    """اطلب آيدي المستخدم المراد حظره ثم نفّذ add_gban."""
    await query.answer()
    prompt = await query.edit_message_text(
        "🚫 <b>حظر مستخدم عالمياً</b>\n\n"
        "أرسل <b>آيدي المستخدم</b> (رقم صحيح) كرد في هذه المحادثة.\n"
        "⏳ المهلة: دقيقتان. أو /إلغاء للتراجع.",
        reply_markup=types.InlineKeyboardMarkup([
            [types.InlineKeyboardButton("❌ إلغاء", callback_data="panel:bans")],
        ]),
    )
    try:
        reply = await app.listen(
            chat_id=query.message.chat.id,
            filters=filters.user(query.from_user.id) & filters.text,
            timeout=_BANS_INPUT_TIMEOUT,
        )
    except asyncio.TimeoutError:
        try:
            await prompt.edit_text(
                "⏰ انتهت المهلة. لم يُنفَّذ أي حظر.",
                reply_markup=types.InlineKeyboardMarkup([
                    [types.InlineKeyboardButton("⬅️ رجوع", callback_data="panel:bans")],
                ]),
            )
        except Exception:
            pass
        return

    try:
        await reply.delete()
    except Exception:
        pass

    text = reply.text.strip()
    if text in ("/إلغاء", "إلغاء", "/cancel", "cancel"):
        await prompt.edit_text(
            "↩️ تم الإلغاء.",
            reply_markup=types.InlineKeyboardMarkup([
                [types.InlineKeyboardButton("⬅️ رجوع", callback_data="panel:bans")],
            ]),
        )
        return

    if not text.lstrip("-").isdigit():
        await prompt.edit_text(
            "❌ آيدي غير صالح. يجب أن يكون رقماً صحيحاً.\n"
            "لم يُنفَّذ أي حظر.",
            reply_markup=types.InlineKeyboardMarkup([
                [types.InlineKeyboardButton("⬅️ رجوع", callback_data="panel:bans")],
            ]),
        )
        return

    uid = int(text)
    if await db.is_gbanned(uid):
        await prompt.edit_text(
            f"⚠️ المستخدم <code>{uid}</code> محظور بالفعل.",
            reply_markup=types.InlineKeyboardMarkup([
                [types.InlineKeyboardButton("⬅️ رجوع", callback_data="panel:bans")],
            ]),
        )
        return

    try:
        await db.add_gban(uid)
        logger.info(f"[bans] gban ← {uid} بواسطة {query.from_user.id}")
    except Exception as e:
        logger.error(f"[bans] فشل add_gban({uid}): {e}", exc_info=True)
        await prompt.edit_text(
            "❌ فشل الحفظ في قاعدة البيانات.",
            reply_markup=types.InlineKeyboardMarkup([
                [types.InlineKeyboardButton("⬅️ رجوع", callback_data="panel:bans")],
            ]),
        )
        return

    gbanned = await _get_gbanned()
    await prompt.edit_text(
        f"✅ تم حظر <code>{uid}</code> عالمياً.\n"
        f"إجمالي المحظورين الآن: <b>{len(gbanned)}</b>",
        reply_markup=types.InlineKeyboardMarkup([
            [types.InlineKeyboardButton("⬅️ رجوع للحظر", callback_data="panel:bans")],
        ]),
    )


# ── رفع حظر ────────────────────────────────────────────────────────────────

async def _handle_bans_del(query: types.CallbackQuery) -> None:
    """اطلب آيدي المستخدم المراد رفع حظره ثم نفّذ del_gban."""
    await query.answer()
    prompt = await query.edit_message_text(
        "✅ <b>رفع الحظر العالمي</b>\n\n"
        "أرسل <b>آيدي المستخدم</b> (رقم صحيح) كرد في هذه المحادثة.\n"
        "⏳ المهلة: دقيقتان. أو /إلغاء للتراجع.",
        reply_markup=types.InlineKeyboardMarkup([
            [types.InlineKeyboardButton("❌ إلغاء", callback_data="panel:bans")],
        ]),
    )
    try:
        reply = await app.listen(
            chat_id=query.message.chat.id,
            filters=filters.user(query.from_user.id) & filters.text,
            timeout=_BANS_INPUT_TIMEOUT,
        )
    except asyncio.TimeoutError:
        try:
            await prompt.edit_text(
                "⏰ انتهت المهلة. لم يُنفَّذ أي تغيير.",
                reply_markup=types.InlineKeyboardMarkup([
                    [types.InlineKeyboardButton("⬅️ رجوع", callback_data="panel:bans")],
                ]),
            )
        except Exception:
            pass
        return

    try:
        await reply.delete()
    except Exception:
        pass

    text = reply.text.strip()
    if text in ("/إلغاء", "إلغاء", "/cancel", "cancel"):
        await prompt.edit_text(
            "↩️ تم الإلغاء.",
            reply_markup=types.InlineKeyboardMarkup([
                [types.InlineKeyboardButton("⬅️ رجوع", callback_data="panel:bans")],
            ]),
        )
        return

    if not text.lstrip("-").isdigit():
        await prompt.edit_text(
            "❌ آيدي غير صالح. يجب أن يكون رقماً صحيحاً.",
            reply_markup=types.InlineKeyboardMarkup([
                [types.InlineKeyboardButton("⬅️ رجوع", callback_data="panel:bans")],
            ]),
        )
        return

    uid = int(text)
    if not await db.is_gbanned(uid):
        await prompt.edit_text(
            f"⚠️ المستخدم <code>{uid}</code> ليس في قائمة الحظر.",
            reply_markup=types.InlineKeyboardMarkup([
                [types.InlineKeyboardButton("⬅️ رجوع", callback_data="panel:bans")],
            ]),
        )
        return

    try:
        await db.del_gban(uid)
        logger.info(f"[bans] ungban ← {uid} بواسطة {query.from_user.id}")
    except Exception as e:
        logger.error(f"[bans] فشل del_gban({uid}): {e}", exc_info=True)
        await prompt.edit_text(
            "❌ فشل الحفظ في قاعدة البيانات.",
            reply_markup=types.InlineKeyboardMarkup([
                [types.InlineKeyboardButton("⬅️ رجوع", callback_data="panel:bans")],
            ]),
        )
        return

    gbanned = await _get_gbanned()
    await prompt.edit_text(
        f"✅ تم رفع حظر <code>{uid}</code>.\n"
        f"إجمالي المحظورين الآن: <b>{len(gbanned)}</b>",
        reply_markup=types.InlineKeyboardMarkup([
            [types.InlineKeyboardButton("⬅️ رجوع للحظر", callback_data="panel:bans")],
        ]),
    )


# ── EXCLUDED_CHATS — عرض وتعديل ────────────────────────────────────────────

def _excluded_keyboard(excluded: list[int]) -> types.InlineKeyboardMarkup:
    """لوحة مفاتيح شاشة المجموعات المستثناة."""
    return types.InlineKeyboardMarkup([
        [
            types.InlineKeyboardButton("➕ إضافة مجموعة", callback_data="panel:bans:excluded:add"),
            types.InlineKeyboardButton("➖ حذف مجموعة",  callback_data="panel:bans:excluded:del"),
        ],
        [types.InlineKeyboardButton("⬅️ رجوع", callback_data="panel:bans")],
    ])


async def _excluded_text(excluded: list[int]) -> str:
    """بناء نص شاشة EXCLUDED_CHATS."""
    lines = ["🏘 <b>المجموعات المستثناة (EXCLUDED_CHATS)</b>\n"]
    if not excluded:
        lines.append("لا توجد مجموعات مستثناة حالياً.")
    else:
        lines.append(f"العدد الكلي: <b>{len(excluded)}</b>\n")
        for i, cid in enumerate(excluded[:_EXCLUDED_PAGE_SIZE], 1):
            lines.append(f"  {i}. <code>{cid}</code>")
        if len(excluded) > _EXCLUDED_PAGE_SIZE:
            lines.append(f"\n… و<b>{len(excluded) - _EXCLUDED_PAGE_SIZE}</b> أخرى.")
    lines.append(
        "\nℹ️ هذه المجموعات مستثناة من المغادرة التلقائية."
    )
    return "\n".join(lines)


async def _handle_excluded_show(query: types.CallbackQuery) -> None:
    """اعرض شاشة EXCLUDED_CHATS."""
    await query.answer()
    excluded = await _get_excluded_chats()
    text = await _excluded_text(excluded)
    await query.edit_message_text(text=text, reply_markup=_excluded_keyboard(excluded))


async def _handle_excluded_add(query: types.CallbackQuery) -> None:
    """اطلب chat_id وأضفه إلى EXCLUDED_CHATS."""
    await query.answer()
    prompt = await query.edit_message_text(
        "➕ <b>إضافة مجموعة مستثناة</b>\n\n"
        "أرسل <b>chat_id</b> (رقم صحيح، سالب عادةً للمجموعات) كرد.\n"
        "⏳ المهلة: دقيقتان. أو /إلغاء للتراجع.",
        reply_markup=types.InlineKeyboardMarkup([
            [types.InlineKeyboardButton("❌ إلغاء", callback_data="panel:bans:excluded")],
        ]),
    )
    try:
        reply = await app.listen(
            chat_id=query.message.chat.id,
            filters=filters.user(query.from_user.id) & filters.text,
            timeout=_BANS_INPUT_TIMEOUT,
        )
    except asyncio.TimeoutError:
        try:
            await prompt.edit_text(
                "⏰ انتهت المهلة. لم يُضَف أي تغيير.",
                reply_markup=types.InlineKeyboardMarkup([
                    [types.InlineKeyboardButton("⬅️ رجوع", callback_data="panel:bans:excluded")],
                ]),
            )
        except Exception:
            pass
        return

    try:
        await reply.delete()
    except Exception:
        pass

    text = reply.text.strip()
    if text in ("/إلغاء", "إلغاء", "/cancel", "cancel"):
        await _handle_excluded_show(query)
        return

    if not text.lstrip("-").isdigit():
        await prompt.edit_text(
            "❌ chat_id غير صالح. يجب أن يكون رقماً صحيحاً.",
            reply_markup=types.InlineKeyboardMarkup([
                [types.InlineKeyboardButton("⬅️ رجوع", callback_data="panel:bans:excluded")],
            ]),
        )
        return

    cid = int(text)
    excluded = await _get_excluded_chats()
    if cid in excluded:
        await prompt.edit_text(
            f"⚠️ <code>{cid}</code> موجودة بالفعل في القائمة.",
            reply_markup=types.InlineKeyboardMarkup([
                [types.InlineKeyboardButton("⬅️ رجوع", callback_data="panel:bans:excluded")],
            ]),
        )
        return

    excluded.append(cid)
    try:
        await _save_excluded_chats(excluded)
        logger.info(f"[bans] excluded_chats += {cid} بواسطة {query.from_user.id}")
    except Exception as e:
        logger.error(f"[bans] فشل حفظ excluded_chats: {e}", exc_info=True)
        await prompt.edit_text(
            "❌ فشل الحفظ في قاعدة البيانات.",
            reply_markup=types.InlineKeyboardMarkup([
                [types.InlineKeyboardButton("⬅️ رجوع", callback_data="panel:bans:excluded")],
            ]),
        )
        return

    text_out = await _excluded_text(excluded)
    text_out = f"✅ تمت إضافة <code>{cid}</code>.\n\n" + text_out
    await prompt.edit_text(text_out, reply_markup=_excluded_keyboard(excluded))


async def _handle_excluded_del(query: types.CallbackQuery) -> None:
    """اطلب chat_id وأزله من EXCLUDED_CHATS."""
    await query.answer()
    excluded = await _get_excluded_chats()
    if not excluded:
        await query.edit_message_text(
            "⚠️ القائمة فارغة، لا يوجد ما يُحذف.",
            reply_markup=types.InlineKeyboardMarkup([
                [types.InlineKeyboardButton("⬅️ رجوع", callback_data="panel:bans:excluded")],
            ]),
        )
        return

    prompt = await query.edit_message_text(
        "➖ <b>حذف مجموعة مستثناة</b>\n\n"
        "أرسل <b>chat_id</b> المراد حذفه كرد.\n"
        "⏳ المهلة: دقيقتان. أو /إلغاء للتراجع.",
        reply_markup=types.InlineKeyboardMarkup([
            [types.InlineKeyboardButton("❌ إلغاء", callback_data="panel:bans:excluded")],
        ]),
    )
    try:
        reply = await app.listen(
            chat_id=query.message.chat.id,
            filters=filters.user(query.from_user.id) & filters.text,
            timeout=_BANS_INPUT_TIMEOUT,
        )
    except asyncio.TimeoutError:
        try:
            await prompt.edit_text(
                "⏰ انتهت المهلة. لم يُنفَّذ أي تغيير.",
                reply_markup=types.InlineKeyboardMarkup([
                    [types.InlineKeyboardButton("⬅️ رجوع", callback_data="panel:bans:excluded")],
                ]),
            )
        except Exception:
            pass
        return

    try:
        await reply.delete()
    except Exception:
        pass

    text = reply.text.strip()
    if text in ("/إلغاء", "إلغاء", "/cancel", "cancel"):
        await _handle_excluded_show(query)
        return

    if not text.lstrip("-").isdigit():
        await prompt.edit_text(
            "❌ chat_id غير صالح. يجب أن يكون رقماً صحيحاً.",
            reply_markup=types.InlineKeyboardMarkup([
                [types.InlineKeyboardButton("⬅️ رجوع", callback_data="panel:bans:excluded")],
            ]),
        )
        return

    cid = int(text)
    if cid not in excluded:
        await prompt.edit_text(
            f"⚠️ <code>{cid}</code> غير موجودة في القائمة.",
            reply_markup=types.InlineKeyboardMarkup([
                [types.InlineKeyboardButton("⬅️ رجوع", callback_data="panel:bans:excluded")],
            ]),
        )
        return

    excluded.remove(cid)
    try:
        await _save_excluded_chats(excluded)
        logger.info(f"[bans] excluded_chats -= {cid} بواسطة {query.from_user.id}")
    except Exception as e:
        logger.error(f"[bans] فشل حفظ excluded_chats: {e}", exc_info=True)
        await prompt.edit_text(
            "❌ فشل الحفظ في قاعدة البيانات.",
            reply_markup=types.InlineKeyboardMarkup([
                [types.InlineKeyboardButton("⬅️ رجوع", callback_data="panel:bans:excluded")],
            ]),
        )
        return

    text_out = await _excluded_text(excluded)
    text_out = f"✅ تم حذف <code>{cid}</code>.\n\n" + text_out
    await prompt.edit_text(text_out, reply_markup=_excluded_keyboard(excluded))


# ── السودورز (Sudo Users) — عرض/إضافة/حذف ───────────────────────────────────
# ملاحظة أمان: لا يمكن حذف config.OWNER_ID من القائمة عبر اللوحة أبداً،
# فهو ليس عنصراً فعلياً في db.get_sudoers() أساساً، ونتحقق من ذلك بشكل
# صريح أيضاً عند الإضافة والحذف كحماية إضافية.

_SUDOERS_INPUT_TIMEOUT = 120   # ثانية — مهلة انتظار إدخال آيدي


def _sudoers_keyboard() -> types.InlineKeyboardMarkup:
    """لوحة مفاتيح شاشة السودورز."""
    return types.InlineKeyboardMarkup([
        [
            types.InlineKeyboardButton("➕ إضافة سودو", callback_data="panel:bans:sudoers:add"),
            types.InlineKeyboardButton("➖ حذف سودو",  callback_data="panel:bans:sudoers:del"),
        ],
        [types.InlineKeyboardButton("⬅️ رجوع", callback_data="panel:bans")],
    ])


async def _sudoers_text(sudoers: list[int]) -> str:
    """بناء نص شاشة السودورز، مع إظهار المالك دائماً في الأعلى (للقراءة فقط)."""
    lines = ["👑 <b>السودورز (Sudo Users)</b>\n"]
    try:
        owner_user = await app.get_users(app.owner)
        owner_label = f"{owner_user.mention} ({app.owner})"
    except Exception:
        owner_label = f"<code>{config.OWNER_ID}</code>"
    lines.append(f"👤 <b>المالك (غير قابل للحذف):</b> {owner_label}\n")

    if not sudoers:
        lines.append("لا يوجد سودورز إضافيون حالياً.")
    else:
        lines.append(f"العدد الكلي: <b>{len(sudoers)}</b>\n")
        for i, uid in enumerate(sudoers, 1):
            try:
                user = await app.get_users(uid)
                lines.append(f"  {i}. {user.mention} (<code>{uid}</code>)")
            except Exception:
                lines.append(f"  {i}. <code>{uid}</code> (حساب محذوف/غير متاح)")
    lines.append(
        "\nℹ️ السودورز يملكون صلاحيات إدارية موسّعة (لا تشمل صلاحيات المالك الحصرية)."
    )
    return "\n".join(lines)


async def _handle_sudoers_show(query: types.CallbackQuery) -> None:
    """اعرض شاشة السودورز."""
    await query.answer()
    sudoers = await _get_sudoers_safe()
    text = await _sudoers_text(sudoers)
    await query.edit_message_text(text=text, reply_markup=_sudoers_keyboard())


async def _resolve_user_id(text: str) -> int | None:
    """
    حوّل نص الإدخال (آيدي رقمي أو @username) إلى user_id.
    يعيد None إذا تعذّر التحويل.
    """
    text = text.strip()
    if text.lstrip("-").isdigit():
        return int(text)
    if text.startswith("@"):
        try:
            user = await app.get_users(text)
            return user.id
        except Exception:
            return None
    return None


async def _handle_sudoers_add(query: types.CallbackQuery) -> None:
    """اطلب آيدي/يوزر المستخدم المراد ترقيته لسودو ثم نفّذه."""
    await query.answer()
    prompt = await query.edit_message_text(
        "➕ <b>إضافة سودو جديد</b>\n\n"
        "أرسل <b>آيدي المستخدم</b> أو <b>@username</b> كرد في هذه المحادثة.\n"
        "⏳ المهلة: دقيقتان. أو /إلغاء للتراجع.",
        reply_markup=types.InlineKeyboardMarkup([
            [types.InlineKeyboardButton("❌ إلغاء", callback_data="panel:bans:sudoers")],
        ]),
    )
    try:
        reply = await app.listen(
            chat_id=query.message.chat.id,
            filters=filters.user(query.from_user.id) & filters.text,
            timeout=_SUDOERS_INPUT_TIMEOUT,
        )
    except asyncio.TimeoutError:
        try:
            await prompt.edit_text(
                "⏰ انتهت المهلة. لم يُنفَّذ أي تغيير.",
                reply_markup=types.InlineKeyboardMarkup([
                    [types.InlineKeyboardButton("⬅️ رجوع", callback_data="panel:bans:sudoers")],
                ]),
            )
        except Exception:
            pass
        return

    try:
        await reply.delete()
    except Exception:
        pass

    text = reply.text.strip()
    if text in ("/إلغاء", "إلغاء", "/cancel", "cancel"):
        await _handle_sudoers_show(query)
        return

    uid = await _resolve_user_id(text)
    if uid is None:
        await prompt.edit_text(
            "❌ إدخال غير صالح. أرسل آيدي رقمي أو @username صحيح.\n"
            "لم يُنفَّذ أي تغيير.",
            reply_markup=types.InlineKeyboardMarkup([
                [types.InlineKeyboardButton("⬅️ رجوع", callback_data="panel:bans:sudoers")],
            ]),
        )
        return

    if uid == config.OWNER_ID:
        await prompt.edit_text(
            "⚠️ هذا المستخدم هو <b>المالك</b> أصلاً ويملك كل الصلاحيات — لا حاجة لإضافته كسودو.",
            reply_markup=types.InlineKeyboardMarkup([
                [types.InlineKeyboardButton("⬅️ رجوع", callback_data="panel:bans:sudoers")],
            ]),
        )
        return

    if uid in app.sudoers:
        await prompt.edit_text(
            f"⚠️ المستخدم <code>{uid}</code> سودو بالفعل.",
            reply_markup=types.InlineKeyboardMarkup([
                [types.InlineKeyboardButton("⬅️ رجوع", callback_data="panel:bans:sudoers")],
            ]),
        )
        return

    try:
        app.sudoers.add(uid)
        app.sudo_filter.update([uid])
        await db.add_sudo(uid)
        logger.info(f"[bans] sudo += {uid} بواسطة {query.from_user.id}")
    except Exception as e:
        logger.error(f"[bans] فشل إضافة سودو {uid}: {e}", exc_info=True)
        await prompt.edit_text(
            "❌ فشل الحفظ في قاعدة البيانات.",
            reply_markup=types.InlineKeyboardMarkup([
                [types.InlineKeyboardButton("⬅️ رجوع", callback_data="panel:bans:sudoers")],
            ]),
        )
        return

    sudoers = await _get_sudoers_safe()
    text_out = await _sudoers_text(sudoers)
    text_out = f"✅ تمت ترقية <code>{uid}</code> إلى سودو.\n\n" + text_out
    await prompt.edit_text(text_out, reply_markup=_sudoers_keyboard())


async def _handle_sudoers_del(query: types.CallbackQuery) -> None:
    """اطلب آيدي/يوزر المستخدم المراد إلغاء سودو منه ثم نفّذه، مع حماية المالك."""
    await query.answer()
    sudoers = await _get_sudoers_safe()
    if not sudoers:
        await query.edit_message_text(
            "⚠️ لا يوجد سودورز لحذفهم حالياً.",
            reply_markup=types.InlineKeyboardMarkup([
                [types.InlineKeyboardButton("⬅️ رجوع", callback_data="panel:bans:sudoers")],
            ]),
        )
        return

    prompt = await query.edit_message_text(
        "➖ <b>حذف سودو</b>\n\n"
        "أرسل <b>آيدي المستخدم</b> أو <b>@username</b> المراد إلغاء صلاحياته كرد.\n"
        "⚠️ لا يمكن حذف المالك (config.OWNER_ID) من القائمة مهما كان الإدخال.\n"
        "⏳ المهلة: دقيقتان. أو /إلغاء للتراجع.",
        reply_markup=types.InlineKeyboardMarkup([
            [types.InlineKeyboardButton("❌ إلغاء", callback_data="panel:bans:sudoers")],
        ]),
    )
    try:
        reply = await app.listen(
            chat_id=query.message.chat.id,
            filters=filters.user(query.from_user.id) & filters.text,
            timeout=_SUDOERS_INPUT_TIMEOUT,
        )
    except asyncio.TimeoutError:
        try:
            await prompt.edit_text(
                "⏰ انتهت المهلة. لم يُنفَّذ أي تغيير.",
                reply_markup=types.InlineKeyboardMarkup([
                    [types.InlineKeyboardButton("⬅️ رجوع", callback_data="panel:bans:sudoers")],
                ]),
            )
        except Exception:
            pass
        return

    try:
        await reply.delete()
    except Exception:
        pass

    text = reply.text.strip()
    if text in ("/إلغاء", "إلغاء", "/cancel", "cancel"):
        await _handle_sudoers_show(query)
        return

    uid = await _resolve_user_id(text)
    if uid is None:
        await prompt.edit_text(
            "❌ إدخال غير صالح. أرسل آيدي رقمي أو @username صحيح.",
            reply_markup=types.InlineKeyboardMarkup([
                [types.InlineKeyboardButton("⬅️ رجوع", callback_data="panel:bans:sudoers")],
            ]),
        )
        return

    # ── حماية صريحة: لا يُحذف المالك من القائمة عبر اللوحة أبداً ──────────────
    if uid == config.OWNER_ID:
        await prompt.edit_text(
            "🔒 <b>غير مسموح.</b> لا يمكن حذف المالك (config.OWNER_ID) من قائمة "
            "السودورز عبر لوحة التحكم.",
            reply_markup=types.InlineKeyboardMarkup([
                [types.InlineKeyboardButton("⬅️ رجوع", callback_data="panel:bans:sudoers")],
            ]),
        )
        return

    if uid not in app.sudoers:
        await prompt.edit_text(
            f"⚠️ <code>{uid}</code> ليس سودو حالياً.",
            reply_markup=types.InlineKeyboardMarkup([
                [types.InlineKeyboardButton("⬅️ رجوع", callback_data="panel:bans:sudoers")],
            ]),
        )
        return

    try:
        app.sudoers.discard(uid)
        app.sudo_filter.update([])
        app.sudo_filter.update(app.sudoers)
        await db.del_sudo(uid)
        logger.info(f"[bans] sudo -= {uid} بواسطة {query.from_user.id}")
    except Exception as e:
        logger.error(f"[bans] فشل حذف سودو {uid}: {e}", exc_info=True)
        await prompt.edit_text(
            "❌ فشل الحفظ في قاعدة البيانات.",
            reply_markup=types.InlineKeyboardMarkup([
                [types.InlineKeyboardButton("⬅️ رجوع", callback_data="panel:bans:sudoers")],
            ]),
        )
        return

    sudoers = await _get_sudoers_safe()
    text_out = await _sudoers_text(sudoers)
    text_out = f"✅ تم حذف <code>{uid}</code> من السودورز.\n\n" + text_out
    await prompt.edit_text(text_out, reply_markup=_sudoers_keyboard())


# ── وضع التنظيف (Clean Mode) — عرض إعلامي فقط ───────────────────────────────
# قرار التصميم: Clean Mode مُخزَّن per-chat (cleanmode_{chat_id} في DB) ويُفعَّل
# بأمر /cleanmode on|off يُكتب داخل المجموعة نفسها من قِبل مشرف فيها. لوحة
# التحكم تُفتَح في الخاص مع المالك دون سياق "مجموعة محدّدة"، فلا يوجد chat_id
# واحد منطقي لعرض زر تبديل فعلي هنا (تبديله من اللوحة سيكون بلا معنى أو
# سيحتاج شاشة اختيار مجموعة كاملة وهي خارج نطاق هذا الطلب). لذلك أضفنا شاشة
# إعلامية فقط تشرح للمالك كيف يُفعَّل الوضع، دون أي تعديل فعلي من هنا.

def _cleanmode_info_keyboard() -> types.InlineKeyboardMarkup:
    return types.InlineKeyboardMarkup([
        [types.InlineKeyboardButton("⬅️ رجوع", callback_data="panel:settings")],
    ])


async def _handle_cleanmode_info(query: types.CallbackQuery) -> None:
    """اعرض شاشة معلوماتية عن طريقة تفعيل/تعطيل الوضع النظيف (Clean Mode)."""
    await query.answer()
    await query.edit_message_text(
        "🧹 <b>الوضع النظيف (Clean Mode)</b>\n\n"
        "هذا الإعداد <b>خاص بكل مجموعة على حدة</b> وليس إعداداً عاماً للبوت، "
        "لذلك لا يُمكن تفعيله أو تعطيله من هنا.\n\n"
        "<b>كيفية التفعيل:</b>\n"
        "يكتب أحد مشرفي المجموعة داخل المجموعة نفسها:\n"
        "• <code>/cleanmode on</code> — لتفعيل الحذف التلقائي لرسالة "
        "«Now Playing» بعد انتهاء كل أغنية.\n"
        "• <code>/cleanmode off</code> — لتعطيله.\n"
        "• <code>/cleanmode</code> بدون قيمة — لعرض الحالة الحالية.\n\n"
        "ℹ️ الأمر متاح لمشرفي المجموعة فقط، ويُطبَّق على تلك المجموعة وحدها.",
        reply_markup=_cleanmode_info_keyboard(),
    )


# ── القائمة السوداء (Blacklist) — مستخدمون + مجموعات ────────────────────────
# تُعيد استخدام db.add_blacklist/del_blacklist/get_blacklisted الموجودة فعلاً
# (تُميّز تلقائياً بين مستخدم/مجموعة حسب علامة chat_id السالبة)، ونحدّث
# app.bl_users (فلتر المستخدمين المحظورين) فوراً بنفس طريقة settings/blacklist.py
# لضمان توافق فوري دون إعادة تشغيل البوت.

_BLACKLIST_INPUT_TIMEOUT = 120   # ثانية


def _blacklist_main_keyboard(n_users: int, n_chats: int) -> types.InlineKeyboardMarkup:
    return types.InlineKeyboardMarkup([
        [types.InlineKeyboardButton(
            f"👤 مستخدمون محظورون ({n_users})", callback_data="panel:bans:blacklist:users")],
        [types.InlineKeyboardButton(
            f"🏘 مجموعات محظورة ({n_chats})", callback_data="panel:bans:blacklist:chats")],
        [types.InlineKeyboardButton("⬅️ رجوع", callback_data="panel:bans")],
    ])


async def _handle_blacklist_main_show(query: types.CallbackQuery) -> None:
    await query.answer()
    bl_users = await _get_bl_users_safe()
    bl_chats = await _get_bl_chats_safe()
    text = (
        "🛑 <b>القائمة السوداء (Blacklist)</b>\n\n"
        "المستخدمون/المجموعات هنا لا يمكنهم استخدام البوت نهائياً.\n\n"
        f"👤 مستخدمون محظورون: <b>{len(bl_users)}</b>\n"
        f"🏘 مجموعات محظورة: <b>{len(bl_chats)}</b>"
    )
    await query.edit_message_text(
        text=text, reply_markup=_blacklist_main_keyboard(len(bl_users), len(bl_chats))
    )


def _bl_list_keyboard(kind: str) -> types.InlineKeyboardMarkup:
    """kind: 'users' أو 'chats'."""
    return types.InlineKeyboardMarkup([
        [
            types.InlineKeyboardButton("➕ إضافة", callback_data=f"panel:bans:blacklist:{kind}:add"),
            types.InlineKeyboardButton("➖ حذف",  callback_data=f"panel:bans:blacklist:{kind}:del"),
        ],
        [types.InlineKeyboardButton("⬅️ رجوع", callback_data="panel:bans:blacklist")],
    ])


async def _bl_users_text(bl_users: list[int]) -> str:
    lines = ["👤 <b>المستخدمون المحظورون من البوت</b>\n"]
    if not bl_users:
        lines.append("لا يوجد مستخدمون محظورون حالياً.")
    else:
        lines.append(f"العدد الكلي: <b>{len(bl_users)}</b>\n")
        for i, uid in enumerate(bl_users[:30], 1):
            try:
                user = await app.get_users(uid)
                lines.append(f"  {i}. {user.mention} (<code>{uid}</code>)")
            except Exception:
                lines.append(f"  {i}. <code>{uid}</code>")
        if len(bl_users) > 30:
            lines.append(f"\n… و<b>{len(bl_users) - 30}</b> آخرين.")
    return "\n".join(lines)


def _bl_chats_text(bl_chats: list[int]) -> str:
    lines = ["🏘 <b>المجموعات المحظورة من البوت</b>\n"]
    if not bl_chats:
        lines.append("لا توجد مجموعات محظورة حالياً.")
    else:
        lines.append(f"العدد الكلي: <b>{len(bl_chats)}</b>\n")
        for i, cid in enumerate(bl_chats[:30], 1):
            lines.append(f"  {i}. <code>{cid}</code>")
        if len(bl_chats) > 30:
            lines.append(f"\n… و<b>{len(bl_chats) - 30}</b> أخرى.")
    return "\n".join(lines)


async def _handle_blacklist_users_show(query: types.CallbackQuery) -> None:
    await query.answer()
    bl_users = await _get_bl_users_safe()
    text = await _bl_users_text(bl_users)
    await query.edit_message_text(text=text, reply_markup=_bl_list_keyboard("users"))


async def _handle_blacklist_chats_show(query: types.CallbackQuery) -> None:
    await query.answer()
    bl_chats = await _get_bl_chats_safe()
    text = _bl_chats_text(bl_chats)
    await query.edit_message_text(text=text, reply_markup=_bl_list_keyboard("chats"))


async def _bl_listen_id(query: types.CallbackQuery, prompt: types.Message) -> str | None:
    """استمع لرد المستخدم النصي، احذفه، وأعد النص أو None عند المهلة."""
    try:
        reply = await app.listen(
            chat_id=query.message.chat.id,
            filters=filters.user(query.from_user.id) & filters.text,
            timeout=_BLACKLIST_INPUT_TIMEOUT,
        )
    except asyncio.TimeoutError:
        return None
    try:
        await reply.delete()
    except Exception:
        pass
    return reply.text.strip()


async def _handle_blacklist_users_add(query: types.CallbackQuery) -> None:
    await query.answer()
    prompt = await query.edit_message_text(
        "➕ <b>حظر مستخدم من البوت</b>\n\n"
        "أرسل <b>آيدي المستخدم</b> أو <b>@username</b> كرد.\n"
        "⏳ المهلة: دقيقتان. أو /إلغاء للتراجع.",
        reply_markup=types.InlineKeyboardMarkup(
            [[types.InlineKeyboardButton("❌ إلغاء", callback_data="panel:bans:blacklist:users")]]
        ),
    )
    text = await _bl_listen_id(query, prompt)
    back_kb = types.InlineKeyboardMarkup(
        [[types.InlineKeyboardButton("⬅️ رجوع", callback_data="panel:bans:blacklist:users")]]
    )
    if text is None:
        return await prompt.edit_text("⏰ انتهت المهلة. لم يُنفَّذ أي حظر.", reply_markup=back_kb)
    if text in ("/إلغاء", "إلغاء", "/cancel", "cancel"):
        return await _handle_blacklist_users_show(query)

    uid = await _resolve_user_id(text)
    if uid is None:
        return await prompt.edit_text("❌ إدخال غير صالح.", reply_markup=back_kb)

    if uid == config.OWNER_ID or uid in app.sudoers:
        return await prompt.edit_text(
            "❌ لا يمكن حظر المالك أو أحد السودورز من البوت.", reply_markup=back_kb
        )

    bl_users = await _get_bl_users_safe()
    if uid in bl_users:
        return await prompt.edit_text(f"⚠️ <code>{uid}</code> محظور بالفعل.", reply_markup=back_kb)

    try:
        app.bl_users.add(uid)
        await db.add_blacklist(uid)
        logger.info(f"[bans] blacklist user += {uid} بواسطة {query.from_user.id}")
    except Exception as e:
        logger.error(f"[bans] فشل حظر المستخدم {uid}: {e}", exc_info=True)
        return await prompt.edit_text("❌ فشل الحفظ في قاعدة البيانات.", reply_markup=back_kb)

    bl_users = await _get_bl_users_safe()
    text_out = f"✅ تم حظر <code>{uid}</code> من البوت.\n\n" + await _bl_users_text(bl_users)
    await prompt.edit_text(text_out, reply_markup=_bl_list_keyboard("users"))


async def _handle_blacklist_users_del(query: types.CallbackQuery) -> None:
    await query.answer()
    bl_users = await _get_bl_users_safe()
    back_kb = types.InlineKeyboardMarkup(
        [[types.InlineKeyboardButton("⬅️ رجوع", callback_data="panel:bans:blacklist:users")]]
    )
    if not bl_users:
        return await query.edit_message_text("⚠️ القائمة فارغة، لا يوجد ما يُحذف.", reply_markup=back_kb)

    prompt = await query.edit_message_text(
        "➖ <b>إلغاء حظر مستخدم</b>\n\n"
        "أرسل <b>آيدي المستخدم</b> أو <b>@username</b> كرد.\n"
        "⏳ المهلة: دقيقتان. أو /إلغاء للتراجع.",
        reply_markup=types.InlineKeyboardMarkup(
            [[types.InlineKeyboardButton("❌ إلغاء", callback_data="panel:bans:blacklist:users")]]
        ),
    )
    text = await _bl_listen_id(query, prompt)
    if text is None:
        return await prompt.edit_text("⏰ انتهت المهلة. لم يُنفَّذ أي تغيير.", reply_markup=back_kb)
    if text in ("/إلغاء", "إلغاء", "/cancel", "cancel"):
        return await _handle_blacklist_users_show(query)

    uid = await _resolve_user_id(text)
    if uid is None:
        return await prompt.edit_text("❌ إدخال غير صالح.", reply_markup=back_kb)

    if uid not in bl_users:
        return await prompt.edit_text(f"⚠️ <code>{uid}</code> غير محظور.", reply_markup=back_kb)

    try:
        app.bl_users.discard(uid)
        await db.del_blacklist(uid)
        logger.info(f"[bans] blacklist user -= {uid} بواسطة {query.from_user.id}")
    except Exception as e:
        logger.error(f"[bans] فشل إلغاء حظر المستخدم {uid}: {e}", exc_info=True)
        return await prompt.edit_text("❌ فشل الحفظ في قاعدة البيانات.", reply_markup=back_kb)

    bl_users = await _get_bl_users_safe()
    text_out = f"✅ تم إلغاء حظر <code>{uid}</code>.\n\n" + await _bl_users_text(bl_users)
    await prompt.edit_text(text_out, reply_markup=_bl_list_keyboard("users"))


async def _handle_blacklist_chats_add(query: types.CallbackQuery) -> None:
    await query.answer()
    prompt = await query.edit_message_text(
        "➕ <b>حظر مجموعة من البوت</b>\n\n"
        "أرسل <b>chat_id</b> (رقم سالب) كرد.\n"
        "⏳ المهلة: دقيقتان. أو /إلغاء للتراجع.",
        reply_markup=types.InlineKeyboardMarkup(
            [[types.InlineKeyboardButton("❌ إلغاء", callback_data="panel:bans:blacklist:chats")]]
        ),
    )
    text = await _bl_listen_id(query, prompt)
    back_kb = types.InlineKeyboardMarkup(
        [[types.InlineKeyboardButton("⬅️ رجوع", callback_data="panel:bans:blacklist:chats")]]
    )
    if text is None:
        return await prompt.edit_text("⏰ انتهت المهلة. لم يُنفَّذ أي حظر.", reply_markup=back_kb)
    if text in ("/إلغاء", "إلغاء", "/cancel", "cancel"):
        return await _handle_blacklist_chats_show(query)

    if not text.lstrip("-").isdigit() or not text.startswith("-"):
        return await prompt.edit_text(
            "❌ chat_id غير صالح. يجب أن يكون رقماً سالباً.", reply_markup=back_kb
        )

    cid = int(text)
    bl_chats = await _get_bl_chats_safe()
    if cid in bl_chats:
        return await prompt.edit_text(f"⚠️ <code>{cid}</code> محظورة بالفعل.", reply_markup=back_kb)

    try:
        await db.add_blacklist(cid)
        logger.info(f"[bans] blacklist chat += {cid} بواسطة {query.from_user.id}")
    except Exception as e:
        logger.error(f"[bans] فشل حظر المجموعة {cid}: {e}", exc_info=True)
        return await prompt.edit_text("❌ فشل الحفظ في قاعدة البيانات.", reply_markup=back_kb)

    bl_chats = await _get_bl_chats_safe()
    text_out = f"✅ تم حظر <code>{cid}</code>.\n\n" + _bl_chats_text(bl_chats)
    await prompt.edit_text(text_out, reply_markup=_bl_list_keyboard("chats"))


async def _handle_blacklist_chats_del(query: types.CallbackQuery) -> None:
    await query.answer()
    bl_chats = await _get_bl_chats_safe()
    back_kb = types.InlineKeyboardMarkup(
        [[types.InlineKeyboardButton("⬅️ رجوع", callback_data="panel:bans:blacklist:chats")]]
    )
    if not bl_chats:
        return await query.edit_message_text("⚠️ القائمة فارغة، لا يوجد ما يُحذف.", reply_markup=back_kb)

    prompt = await query.edit_message_text(
        "➖ <b>إلغاء حظر مجموعة</b>\n\n"
        "أرسل <b>chat_id</b> كرد.\n"
        "⏳ المهلة: دقيقتان. أو /إلغاء للتراجع.",
        reply_markup=types.InlineKeyboardMarkup(
            [[types.InlineKeyboardButton("❌ إلغاء", callback_data="panel:bans:blacklist:chats")]]
        ),
    )
    text = await _bl_listen_id(query, prompt)
    if text is None:
        return await prompt.edit_text("⏰ انتهت المهلة. لم يُنفَّذ أي تغيير.", reply_markup=back_kb)
    if text in ("/إلغاء", "إلغاء", "/cancel", "cancel"):
        return await _handle_blacklist_chats_show(query)

    if not text.lstrip("-").isdigit():
        return await prompt.edit_text("❌ chat_id غير صالح.", reply_markup=back_kb)

    cid = int(text)
    if cid not in bl_chats:
        return await prompt.edit_text(f"⚠️ <code>{cid}</code> غير محظورة.", reply_markup=back_kb)

    try:
        await db.del_blacklist(cid)
        logger.info(f"[bans] blacklist chat -= {cid} بواسطة {query.from_user.id}")
    except Exception as e:
        logger.error(f"[bans] فشل إلغاء حظر المجموعة {cid}: {e}", exc_info=True)
        return await prompt.edit_text("❌ فشل الحفظ في قاعدة البيانات.", reply_markup=back_kb)

    bl_chats = await _get_bl_chats_safe()
    text_out = f"✅ تم إلغاء حظر <code>{cid}</code>.\n\n" + _bl_chats_text(bl_chats)
    await prompt.edit_text(text_out, reply_markup=_bl_list_keyboard("chats"))


# ==============================================================================
# قسم 📢 البرودكاست (panel:broadcast)
# ==============================================================================
#
# قرار تصميمي:
# هذا القسم توجيهي فقط — لا يُنفِّذ إرسال البرودكاست من داخل اللوحة.
# السبب: أمر /broadcast الحالي (broadcast.py) يدعم الرد على رسائل وسائط
# (بما فيها الألبومات media_group)، وعدة أعلام (-user / -nochat / -pin /
# -pinloud / -copy) تتغيّر بها طريقة الإرسال جذرياً. إعادة بناء هذا داخل آلية
# "انتظار رد" نصّية في اللوحة سيفقد دعم الوسائط/الألبومات أو يتطلب تكرار جزء
# كبير من منطق broadcast.py، وهو ما طلب المستخدم تفسيره صريحاً عند اتخاذه.
# لذلك تم اختيار المسار الآمن الافتراضي: زر توجيهي + شرح كامل للأوامر.
# ==============================================================================

def _broadcast_keyboard() -> types.InlineKeyboardMarkup:
    """لوحة مفاتيح شاشة البرودكاست — توجيهية فقط، بدون تنفيذ."""
    return types.InlineKeyboardMarkup([
        _back_row("panel:main"),
    ])


# ==============================================================================
# قسم 🚪 مغادرة المجموعات (panel:leave)
# ==============================================================================
#
# يُعاد استخدام منطق المغادرة من leave.py مباشرةً (الدوال _do_leave_chat و
# _do_leave_all_idle) عبر استيراد الوحدة ديناميكياً — بنفس الأسلوب المتّبع مع
# restart.py في قسم 🛠️ الصيانة والنظام. لا يُعاد كتابة أي منطق مغادرة هنا.
# ==============================================================================

_LEAVE_INPUT_TIMEOUT = 120  # ثانية — مهلة انتظار إدخال chat_id


def _get_leave_module():
    """يستورد وحدة leave.py (محمَّلة مسبقاً عادةً) بنفس أسلوب restart.py."""
    import sys as _sys  # noqa: PLC0415
    mod = _sys.modules.get("UltraMusic.plugins.admin-controls.leave")
    if mod is None:
        import importlib as _il  # noqa: PLC0415
        mod = _il.import_module("UltraMusic.plugins.admin-controls.leave")
    return mod


def _leave_keyboard() -> types.InlineKeyboardMarkup:
    """لوحة مفاتيح شاشة مغادرة المجموعات."""
    return types.InlineKeyboardMarkup([
        [types.InlineKeyboardButton("🎯 مغادرة مجموعة محدّدة",
                                    callback_data="panel:leave:specific")],
        [types.InlineKeyboardButton("🧹 مغادرة المجموعات الخاملة",
                                    callback_data="panel:leave:idle")],
        _back_row("panel:main"),
    ])


async def _handle_leave_specific(query: types.CallbackQuery) -> None:
    """اطلب chat_id ثم نفّذ مغادرة تلك المجموعة (بوت + مساعد) عبر leave.py."""
    await query.answer()
    prompt = await query.edit_message_text(
        "🎯 <b>مغادرة مجموعة محدّدة</b>\n\n"
        "أرسل <b>chat_id</b> المجموعة (رقم سالب عادةً) كرد في هذه المحادثة.\n"
        "⏳ المهلة: دقيقتان. أو /إلغاء للتراجع.",
        reply_markup=types.InlineKeyboardMarkup([
            [types.InlineKeyboardButton("❌ إلغاء", callback_data="panel:leave")],
        ]),
    )
    try:
        reply = await app.listen(
            chat_id=query.message.chat.id,
            filters=filters.user(query.from_user.id) & filters.text,
            timeout=_LEAVE_INPUT_TIMEOUT,
        )
    except asyncio.TimeoutError:
        try:
            await prompt.edit_text(
                "⏰ انتهت المهلة. لم يُنفَّذ أي مغادرة.",
                reply_markup=types.InlineKeyboardMarkup([
                    [types.InlineKeyboardButton("⬅️ رجوع", callback_data="panel:leave")],
                ]),
            )
        except Exception:
            pass
        return

    try:
        await reply.delete()
    except Exception:
        pass

    text = reply.text.strip()
    back_kb = types.InlineKeyboardMarkup([
        [types.InlineKeyboardButton("⬅️ رجوع", callback_data="panel:leave")],
    ])

    if text in ("/إلغاء", "إلغاء", "/cancel", "cancel"):
        await prompt.edit_text("↩️ تم الإلغاء.", reply_markup=back_kb)
        return

    if not text.lstrip("-").isdigit():
        await prompt.edit_text(
            "❌ chat_id غير صالح. يجب أن يكون رقماً صحيحاً.\nلم يُنفَّذ أي مغادرة.",
            reply_markup=back_kb,
        )
        return

    cid = int(text)
    leave_mod = _get_leave_module()
    try:
        success, err = await leave_mod._do_leave_chat(cid)
    except Exception as e:
        logger.error(f"[leave] خطأ غير متوقع عند مغادرة {cid}: {e}", exc_info=True)
        await prompt.edit_text(
            f"❌ حدث خطأ غير متوقع: {e}", reply_markup=back_kb,
        )
        return

    if success:
        logger.info(f"[leave] غادر البوت/المساعد {cid} بواسطة {query.from_user.id} عبر اللوحة")
        await prompt.edit_text(
            f"✅ تمت مغادرة <code>{cid}</code> بنجاح (بوت + مساعد إن وُجد).",
            reply_markup=back_kb,
        )
    else:
        await prompt.edit_text(
            f"❌ فشلت مغادرة <code>{cid}</code>:\n<code>{html.escape(err or '')}</code>",
            reply_markup=back_kb,
        )


async def _handle_leave_idle_confirm(query: types.CallbackQuery) -> None:
    """اعرض تأكيداً قبل تنفيذ مغادرة كل المجموعات الخاملة."""
    await query.answer()
    await query.edit_message_text(
        "⚠️ <b>تأكيد مغادرة المجموعات الخاملة</b>\n\n"
        "سيغادر كل المساعدين أي مجموعة لا تحتوي مكالمة نشطة حالياً "
        "(باستثناء المجموعات والقنوات المستثناة).\n"
        "هذا الإجراء قد يؤثر على عدد كبير من المجموعات. متأكد؟",
        reply_markup=types.InlineKeyboardMarkup([
            [
                types.InlineKeyboardButton("✅ تأكيد", callback_data="panel:leave:idle:yes"),
                types.InlineKeyboardButton("❌ إلغاء", callback_data="panel:leave"),
            ],
        ]),
    )


async def _handle_leave_idle_confirmed(query: types.CallbackQuery) -> None:
    """نفّذ مغادرة المجموعات الخاملة فعلياً عبر leave.py (لا تكرار للمنطق)."""
    await query.answer("🧹 جارٍ المغادرة...", show_alert=False)
    leave_mod = _get_leave_module()
    try:
        total_left = await leave_mod._do_leave_all_idle()
        logger.info(f"[leave] مغادرة جماعية للخاملة: {total_left} مجموعة — بواسطة {query.from_user.id}")
    except Exception as e:
        logger.error(f"[leave] خطأ في مغادرة المجموعات الخاملة: {e}", exc_info=True)
        await query.edit_message_text(
            f"❌ حدث خطأ أثناء المغادرة: {e}",
            reply_markup=types.InlineKeyboardMarkup([_back_row("panel:leave")]),
        )
        return

    await query.edit_message_text(
        f"✅ <b>اكتملت العملية</b>\n\nغادر المساعدون <b>{total_left}</b> مجموعة خاملة.",
        reply_markup=types.InlineKeyboardMarkup([_back_row("panel:leave")]),
    )
