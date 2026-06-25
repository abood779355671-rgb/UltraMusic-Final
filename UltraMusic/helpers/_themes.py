# ==============================================================================
# _themes.py - Message/Button Themes for Now-Playing Cards
# ==============================================================================
# يوفّر هذا الملف قاموس THEMES بأربعة ثيمات جاهزة لرسالة "بدأ البث المطلوب"،
# بالإضافة إلى دالة get_theme() لاسترجاع الثيم المطلوب بأمان.
#
# ملاحظات هامة حول الحقول المستخدمة:
# - الحقول المتاحة فعلاً على Media/Track (helpers/_dataclass.py) هي:
#   id, duration, duration_sec, file_path, message_id, title, url, time,
#   user, is_live, video (و channel_name, thumbnail, view_count على Track فقط).
#   لا يوجد أي حقل اسمه "اسم الطالب"؛ تم استخدام `user` (مُرسل الطلب) في
#   جميع الثيمات لأنه الحقل الوحيد المتاح لهذا الغرض.
# - قالب "classic" هو نفسه نص locales/ar.json["play_media"] حرفياً، ويُستخدم
#   بالضبط بنفس ترتيب التهيئة المستخدم في core/calls.py:
#       text.format(media.url, media.title, media.duration, media.user)
#   أي {0}=url, {1}=title, {2}=duration, {3}=user.
# - تخطيط "classic" لـ button_layout يطابق حرفياً تخطيط compact في
#   helpers/_inline.py -> Inline.controls() (وهو التخطيط الافتراضي فعلياً
#   هناك، إذ لا يوجد ملف helpers/buttons.py في المشروع: الاسم buttons هو
#   اسم بديل (alias) لِـ Inline() المُعرّف في helpers/__init__.py).
# - get_theme() أصبحت دالة async: تبحث أولاً في THEMES الثابت، ثم في كولكشن
#   custom_themes عبر db.get_custom_theme() (انظر core/mongo.py)، وعند فشل كل
#   شيء (ثيم غير موجود، أو DB غير متاحة) تُرجع THEMES["classic"] دون استثناء.
# ==============================================================================

import logging

from UltraMusic import db

logger = logging.getLogger(__name__)


THEMES = {
    # ─────────────────────────────────────────────────────────────────────
    # classic: نفس نص locales/ar.json["play_media"] حرفياً + نفس تخطيط
    # controls() الافتراضي (compact) في helpers/_inline.py حرفياً.
    # ─────────────────────────────────────────────────────────────────────
    "classic": {
        "id": "classic",
        "name_ar": "كلاسيكي",
        "message_template": (
            "<blockquote>🔴 <b>بدأ البث المطلوب</b></blockquote>\n"
            "<blockquote>➤ <b>العنوان :</b> <a href={0}>{1}</a>\n"
            "➤ <b>المدة :</b> {2} دقيقة\n"
            "➤ <b>طُلب بواسطة :</b> {3}</blockquote>"
        ),
        "button_layout": [
            ["▷", "II", "↻", "‣‣I", "▢"],
            ["ᴅᴇʟᴇᴛᴇ"],
        ],
        "show_thumbnail": True,
        "emoji_set": ["🔴"],
    },

    # ─────────────────────────────────────────────────────────────────────
    # minimal: سطر أو سطرين فقط، وأزرار رمزية فقط (بدون زر الحذف النصي).
    # ─────────────────────────────────────────────────────────────────────
    "minimal": {
        "id": "minimal",
        "name_ar": "مبسّط",
        "message_template": (
            "🎵 {1} — {2}\n"
            "بواسطة {3}"
        ),
        "button_layout": [
            ["▷", "II", "‣‣I", "▢"],
        ],
        "show_thumbnail": True,
        "emoji_set": ["🎵"],
    },

    # ─────────────────────────────────────────────────────────────────────
    # luxury: حدود يونيكود، إيموجيات أكثر، و <b> حول اسم المُرسل (user).
    # ─────────────────────────────────────────────────────────────────────
    "luxury": {
        "id": "luxury",
        "name_ar": "فاخر",
        "message_template": (
            "┌─────────────────────┐\n"
            "│  ✨ <b>بدأ البث المطلوب</b> ✨\n"
            "└─────────────────────┘\n"
            "│ 🎶 <b>العنوان :</b> <a href={0}>{1}</a>\n"
            "│ ⏱ <b>المدة :</b> {2} دقيقة\n"
            "│ 👤 <b>طُلب بواسطة :</b> <b>{3}</b>\n"
            "└─────────────────────┘"
        ),
        "button_layout": [
            ["▷", "II", "↻"],
            ["‣‣I", "▢", "ᴅᴇʟᴇᴛᴇ"],
        ],
        "show_thumbnail": True,
        "emoji_set": ["✨", "🎶", "⏱", "👤"],
    },

    # ─────────────────────────────────────────────────────────────────────
    # no_thumbnail: نص classic بلا أي صورة (show_thumbnail=False).
    # ─────────────────────────────────────────────────────────────────────
    "no_thumbnail": {
        "id": "no_thumbnail",
        "name_ar": "بدون صورة",
        "message_template": (
            "<blockquote>🔴 <b>بدأ البث المطلوب</b></blockquote>\n"
            "<blockquote>➤ <b>العنوان :</b> <a href={0}>{1}</a>\n"
            "➤ <b>المدة :</b> {2} دقيقة\n"
            "➤ <b>طُلب بواسطة :</b> {3}</blockquote>"
        ),
        "button_layout": [
            ["▷", "II", "↻", "‣‣I", "▢"],
            ["ᴅᴇʟᴇᴛᴇ"],
        ],
        "show_thumbnail": False,
        "emoji_set": ["🔴"],
    },
}


async def get_theme(theme_id: str) -> dict:
    """إرجاع الثيم المطابق لـ theme_id بأمان.

    ترتيب البحث:
    1) قاموس THEMES الثابت (الثيمات المدمجة).
    2) كولكشن custom_themes عبر DB (الثيمات المخصصة المحفوظة من لوحة التحكم).
    3) عند فشل كل ما سبق: تُرجع الدالة THEMES["classic"] دون أن يخرج أي
       استثناء منها على الإطلاق، مع تسجيل تحذير عبر logger.warning.
    """
    try:
        if not isinstance(theme_id, str):
            logger.warning(
                f"[get_theme] theme_id غير صالح (ليس نصاً): {theme_id!r}، "
                f"الرجوع إلى الثيم classic."
            )
            return THEMES["classic"]

        theme = THEMES.get(theme_id)
        if theme is not None:
            return theme

        # لم يُوجد الثيم بين الثيمات الثابتة، نبحث في الثيمات المخصصة عبر DB.
        try:
            custom = await db.get_custom_theme(theme_id)
            if custom:
                return custom
        except Exception as _db_err:
            logger.warning(
                f"[get_theme] تعذّر البحث في custom_themes عن '{theme_id}': "
                f"{_db_err}، الرجوع إلى الثيم classic."
            )
            return THEMES["classic"]

        logger.warning(
            f"[get_theme] الثيم '{theme_id}' غير موجود في THEMES ولا في "
            f"custom_themes، الرجوع إلى الثيم classic."
        )
        return THEMES["classic"]
    except Exception as e:
        try:
            logger.warning(
                f"[get_theme] خطأ غير متوقع أثناء جلب الثيم '{theme_id}': {e}، "
                f"الرجوع إلى الثيم classic."
            )
        except Exception:
            # حتى لو فشل التسجيل نفسه، لا يجب أن يخرج أي استثناء من هذه الدالة.
            pass
        return THEMES["classic"]


def build_now_playing_text(theme: dict, url: str, title: str, duration: str, user: str) -> str:
    """بناء نص رسالة 'يُشغَّل الآن' من قالب الثيم المحدد.

    المعاملات مرتّبة بنفس ترتيب .format() المستخدم في core/calls.py:
        {0}=url, {1}=title, {2}=duration, {3}=user

    تُستخدم من مكانين:
        - core/calls.py  → بناء رسالة التشغيل الفعلي.
        - control_panel.py → بناء رسالة المعاينة في شاشة panel:themes:preview.
    """
    return theme["message_template"].format(url, title, duration, user)

