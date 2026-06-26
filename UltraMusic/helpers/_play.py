# ==============================================================================
# _play.py - Play Command Helper & Validator
# ==============================================================================
# This file contains the @checkUB decorator used by play commands.
# Validates:
# - User permissions (only real users, not anonymous admins)
# - Chat type (only supergroups)
# - Command syntax (query or reply required)
# - Queue limits
# - YouTube URL validity
#
# This decorator ensures all play commands have proper validation before execution.
# ==============================================================================

import asyncio
import time as _time
from collections import defaultdict, deque

from pyrogram import enums, errors, types

from UltraMusic import app, config, db, logger, queue, yt


# ── Play rate limiter ──────────────────────────────────────────────────────────
# Keyed by (chat_id, user_id) → deque of monotonic timestamps.
# Each timestamp represents one accepted play request inside the sliding window.
_play_timestamps: dict = defaultdict(deque)


def _is_rate_limited(chat_id: int, user_id: int) -> bool:
    """Return True (and do NOT record the request) when the user has exceeded
    the per-chat rate limit, False (and record the request) otherwise.

    Controlled by config.RATE_LIMIT_ENABLED / RATE_LIMIT_MAX / RATE_LIMIT_WINDOW.
    Sudoers are never rate-limited.
    """
    if not config.RATE_LIMIT_ENABLED:
        return False

    window = config.RATE_LIMIT_WINDOW
    max_req = config.RATE_LIMIT_MAX
    now = _time.monotonic()
    key = (chat_id, user_id)
    dq = _play_timestamps[key]

    # Evict timestamps that have slid out of the window
    while dq and now - dq[0] > window:
        dq.popleft()

    if len(dq) >= max_req:
        return True   # over limit – do NOT append; don't reset the window

    dq.append(now)
    return False


# Arabic play commands mapped to their behaviour flags: (force, cplay, video)
# This replaces the old English name parsing (startswith "c", endswith "force"...).
PLAY_VARIANTS = {
    "تشغيل": (False, False, False),          # play
    "تشغيل_فوري": (True, False, False),       # playforce
    "تشغيل_قناة": (False, True, False),       # cplay
    "تشغيل_قناة_فوري": (True, True, False),   # cplayforce
    "فيديو": (False, False, True),            # vplay
    "فيديو_فوري": (True, False, True),        # vplayforce
    "فيديو_قناة": (False, True, True),        # cvplay
    "فيديو_قناة_فوري": (True, True, True),    # cvplayforce
}

# List of play command triggers (used by the play handler registration).
PLAY_COMMANDS = list(PLAY_VARIANTS.keys())


def checkUB(play):
    async def wrapper(_, m: types.Message):
        async def safe_reply(text):
            """Safely send reply, return None if chat doesn't allow messages"""
            try:
                return await m.reply_text(text)
            except (errors.ChatWriteForbidden, errors.ChatSendPlainForbidden):
                # Chat doesn't allow text messages - silently return
                return None
            except Exception:
                return None

        _admin_msg = (
            "<blockquote><b>🔐 صلاحيات المشرف مطلوبة</b></blockquote>\n\n"
            "<blockquote>لتشغيل الموسيقى في هذه المجموعة، أحتاج أن أكون <b>مشرفاً</b>.\n\n"
            "<b>الصلاحيات المطلوبة:</b>\n"
            "• إدارة الدردشات الصوتية\n"
            "• دعوة المستخدمين عبر رابط\n"
            "• حذف الرسائل\n\n"
            "يرجى ترقيتي كمشرف مع الصلاحيات المطلوبة.</blockquote>"
        )

        if not m.from_user:
            await safe_reply(m.lang["play_user_invalid"])
            return

        if m.chat.type != enums.ChatType.SUPERGROUP:
            await safe_reply(m.lang["play_chat_invalid"])
            return await app.leave_chat(m.chat.id)

        # ── Rate limiting (sudoers are exempt) ────────────────────────────────
        if m.from_user.id not in app.sudoers and _is_rate_limited(m.chat.id, m.from_user.id):
            await safe_reply(
                m.lang["play_rate_limit"].format(
                    config.RATE_LIMIT_MAX, config.RATE_LIMIT_WINDOW
                )
            )
            return

        if not m.reply_to_message and (
            len(m.command) < 2 or (len(m.command)
                                   == 2 and m.command[1] == "-f")
        ):
            await safe_reply(m.lang["play_usage"])
            return

        try:
            queue_limit = await db.get_setting("QUEUE_LIMIT", config.QUEUE_LIMIT)
        except Exception as _e:
            logger.warning(f"[settings] تعذّر قراءة QUEUE_LIMIT من DB، سيُستخدم الافتراضي ({config.QUEUE_LIMIT}): {_e}")
            queue_limit = config.QUEUE_LIMIT
        if len(queue.get_queue(m.chat.id)) >= queue_limit:
            await safe_reply(m.lang["play_queue_full"].format(queue_limit))
            return

        command = m.command[0]
        base_force, cplay, video_requested = PLAY_VARIANTS.get(
            command, (False, False, False)
        )
        # Allow the "-f" argument to force-play any variant.
        force = base_force or (len(m.command) > 1 and "-f" in m.command[1])

        if video_requested and not await db.get_vplay_enabled():
            await safe_reply(m.lang["play_video_disabled"])
            return
        video = video_requested
        
        url = yt.url(m)
        # Only validate URL if not replying to media (Telegram files have t.me URLs)
        if url and not m.reply_to_message and not yt.valid(url):
            return await m.reply_text(m.lang["play_unsupported"])

        play_mode = await db.get_play_mode(m.chat.id)
        if play_mode or force:
            adminlist = await db.get_admins(m.chat.id)
            if (
                m.from_user.id not in adminlist
                and not await db.is_auth(m.chat.id, m.from_user.id)
                and not m.from_user.id in app.sudoers
            ):
                await safe_reply(m.lang["play_admin"])
                return

        if m.chat.id not in db.active_calls:
            client = await db.get_client(m.chat.id)
            if client is None:
                await safe_reply(m.lang["no_client"])
                return
            try:
                member = await app.get_chat_member(m.chat.id, client.id)
                if member.status in [
                    enums.ChatMemberStatus.BANNED,
                    enums.ChatMemberStatus.RESTRICTED,
                ]:
                    try:
                        await app.unban_chat_member(
                            chat_id=m.chat.id, user_id=client.id
                        )
                    except Exception:
                        await safe_reply(
                            m.lang["play_banned"].format(
                                app.name,
                                client.id,
                                client.mention,
                                f"@{client.username}" if client.username else None,
                            )
                        )
                        return
            except errors.ChatAdminRequired:
                await safe_reply(_admin_msg)
                return
            except errors.UserNotParticipant:
                if m.chat.username:
                    invite_link = m.chat.username
                    try:
                        await client.resolve_peer(invite_link)
                    except Exception:
                        pass
                else:
                    try:
                        invite_link = (await app.get_chat(m.chat.id)).invite_link
                        if not invite_link:
                            invite_link = await app.export_chat_invite_link(m.chat.id)
                    except errors.ChatAdminRequired:
                        await safe_reply(_admin_msg)
                        return
                    except Exception as ex:
                        await safe_reply(
                            m.lang["play_invite_error"].format(
                                type(ex).__name__)
                        )
                        return

                umm = await safe_reply(m.lang["play_invite"].format(app.name))
                if umm:
                    await asyncio.sleep(2)
                try:
                    await client.join_chat(invite_link)
                except errors.UserAlreadyParticipant:
                    pass
                except errors.InviteRequestSent:
                    try:
                        await client.approve_chat_join_request(m.chat.id, client.id)
                    except errors.ChatAdminRequired:
                        if umm:
                            try:
                                await umm.edit_text(_admin_msg)
                            except Exception:
                                pass
                        return
                    except Exception as ex:
                        if umm:
                            try:
                                await umm.edit_text(
                                    m.lang["play_invite_error"].format(
                                        type(ex).__name__)
                                )
                            except Exception:
                                pass
                        return
                except errors.ChatAdminRequired:
                    if umm:
                        try:
                            await umm.edit_text(_admin_msg)
                        except Exception:
                            pass
                    return
                except Exception as ex:
                    if umm:
                        try:
                            await umm.edit_text(
                                m.lang["play_invite_error"].format(type(ex).__name__)
                            )
                        except Exception:
                            pass
                    return

                if umm:
                    try:
                        await umm.delete()
                    except Exception:
                        pass
                await client.resolve_peer(m.chat.id)

        try:
            await m.delete()
        except Exception:
            pass

        return await play(_, m, force, url, cplay, video)

    return wrapper
