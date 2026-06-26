# ==============================================================================
# play.py - Main Play Command Handler
# ==============================================================================
# This is the core plugin that handles all play-related commands:
# - /play <query> - Play audio from YouTube search or URL
# - /vplay <query> - Play video in the voice chat (when enabled)
# - /playforce - Force play (skip queue and play immediately)
# - /vplayforce - Force video playback (skip queue)
# - /cplay - Play in connected channel
# - /cvplay - Play video in connected channel (when enabled)
# 
# Supports:
# - YouTube search queries
# - YouTube URLs (videos and playlists)
# - Telegram audio files (via reply)
# - Queue management
# - Channel play mode
# ==============================================================================

from pyrogram import filters
from pyrogram import types
try:
    from pyrogram.enums import ReactionEmoji
except ImportError:
    ReactionEmoji = None  # kurigram لا يدعم ReactionEmoji
from pyrogram.errors import FloodWait, MessageIdInvalid, MessageDeleteForbidden, ChatSendPlainForbidden, ChatWriteForbidden

from UltraMusic import tune, app, config, db, lang, queue, tg, yt
from UltraMusic.helpers import buttons, command, utils
from UltraMusic.helpers._play import checkUB, PLAY_COMMANDS
import asyncio
import logging

logger = logging.getLogger(__name__)


async def safe_edit(message, text, **kwargs):
    """
    Safely edit a message with proper error handling for common Telegram API errors.
    
    Args:
        message: The message object to edit
        text: New text content
        **kwargs: Additional arguments for edit_text
        
    Returns:
        True if successful, False otherwise
    """
    try:
        await message.edit_text(text, **kwargs)
        return True
    except FloodWait as e:
        await asyncio.sleep(e.value)
        try:
            await message.edit_text(text, **kwargs)
            return True
        except (MessageIdInvalid, MessageDeleteForbidden, Exception):
            return False
    except (MessageIdInvalid, MessageDeleteForbidden):
        # Message was deleted or became invalid - this is expected
        return False
    except Exception:
        # Other errors - log but don't crash
        return False


async def safe_reply(message, text, **kwargs):
    """
    Safely send a reply message with proper error handling for media-only chats.
    
    Args:
        message: The message object to reply to
        text: Text content to send
        **kwargs: Additional arguments for reply_text
        
    Returns:
        The sent message object if successful, None otherwise
    """
    try:
        return await message.reply_text(text, **kwargs)
    except (ChatSendPlainForbidden, ChatWriteForbidden):
        logger.warning(f"Cannot send text in chat {message.chat.id} (chat write forbidden)")
        return None
    except Exception as e:
        logger.error(f"Failed to send reply: {e}")
        return None


def playlist_to_queue(chat_id: int, tracks: list) -> str:
    """
    Add multiple tracks to queue and format them as a message.
    
    Args:
        chat_id: The chat ID where queue is managed
        tracks: List of Track objects to add
        
    Returns:
        Formatted string listing all added tracks
    """
    text = "<blockquote expandable>"
    for track in tracks:
        pos = queue.add(chat_id, track)  # Add track to queue (returns 0-based index)
        text += f"<b>{pos}.</b> {track.title}\n"  # Show actual queue position
    # Truncate on the last complete line that fits within the limit
    LIMIT = 1948
    if len(text) > LIMIT:
        cut = text.rfind("\n", 0, LIMIT)
        text = text[:cut if cut != -1 else LIMIT]
    text += "</blockquote>"
    return text

@app.on_message(
    command(PLAY_COMMANDS)
    & filters.group
    & ~app.bl_users
)
@lang.language()
@checkUB
async def play_hndlr(
    _,
    m: types.Message,
    force: bool = False,
    url: str = None,
    cplay: bool = False,
    video: bool = False,
) -> None:
    # Auto-delete command message
    try:
        await m.delete()
    except Exception:
        pass
    
    # Handle channel play mode
    chat_id = m.chat.id
    message_chat_id = m.chat.id  # Store original group chat ID for thumbnail
    if cplay:
        channel_id = await db.get_cmode(m.chat.id)
        if channel_id is None:
            return await safe_reply(m,
                "<blockquote>❌ تشغيل القناة غير مفعّل.\n\n"
                "للتفعيل للقناة المرتبطة:\n"
                "`ربط_القناة linked`\n\n"
                "للتفعيل لأي قناة:\n"
                "`ربط_القناة [معرّف_القناة]`</blockquote>"
            )
        try:
            chat = await app.get_chat(channel_id)
            chat_id = channel_id
        except:
            await db.set_cmode(m.chat.id, None)
            return await safe_reply(m,
                "<blockquote>❌ فشل الوصول إلى القناة.\n\n"
                "تأكّد من أنني مشرف في القناة وأن تشغيل القناة مُعدّ بشكل صحيح.</blockquote>"
            )
        
        # Auto-join assistant to channel if not already a member
        client = await db.get_client(channel_id)
        try:
            # Check if assistant is in the channel
            await app.get_chat_member(channel_id, client.id)
        except Exception:
            # Assistant not in channel, try to join
            try:
                # For channels, we need an invite link
                if chat.username:
                    invite_link = chat.username
                else:
                    # Try to get/create invite link
                    try:
                        invite_link = chat.invite_link
                        if not invite_link:
                            invite_link = await app.export_chat_invite_link(channel_id)
                    except Exception:
                        return await safe_reply(m,
                            f"<blockquote>❌ المساعد ليس في القناة!\n\n"
                            f"يرجى إضافة @{client.username if client.username else client.mention} "
                            f"إلى القناة كمشرف مع صلاحيات الدردشة الصوتية.</blockquote>"
                        )
                
                # Show joining message
                join_msg = await safe_reply(m,
                    f"<blockquote>🔄 جارٍ انضمام المساعد إلى القناة...</blockquote>"
                )
                
                # Try to join the channel
                await client.join_chat(invite_link)
                await asyncio.sleep(1)  # Give it time to fully join
                
                # Delete joining message
                try:
                    await join_msg.delete()
                except:
                    pass
                    
            except Exception as e:
                error_str = str(e)
                return await safe_reply(m,
                    f"<blockquote>❌ فشل انضمام المساعد إلى القناة!\n\n"
                    f"يرجى إضافة @{client.username if client.username else client.mention} "
                    f"يدوياً إلى القناة كمشرف مع صلاحيات الدردشة الصوتية.\n\n"
                    f"الخطأ: {error_str}</blockquote>"
                )

    # Select emoji for this play session
    play_emoji = m.lang["play_emoji"]
    
    try:
        sent = await safe_reply(m, m.lang["play_searching"].format(play_emoji))
    except FloodWait as e:
        await asyncio.sleep(e.value)
        try:
            sent = await safe_reply(m, m.lang["play_searching"].format(play_emoji))
        except FloodWait as e2:
            # If still flood wait, wait longer and give up gracefully
            await asyncio.sleep(e2.value)
            return  # Abort silently
        except Exception:
            return  # Abort silently
    except Exception:
        return  # If we can't even send initial message, abort
    
    mention = m.from_user.mention
    media = tg.get_media(m.reply_to_message) if m.reply_to_message else None
    tracks = []
    file = None  # Initialize file variable

    # Check media first (Telegram files) before URL extraction
    if media:
        setattr(sent, "lang", m.lang)
        file = await tg.download(m.reply_to_message, sent)

    elif url:
        if "playlist" in url:
            await safe_edit(sent, m.lang["playlist_fetch"])
            try:
                try:
                    playlist_limit = await db.get_setting("PLAYLIST_LIMIT", config.PLAYLIST_LIMIT)
                except Exception as _e:
                    logger.warning(f"[settings] تعذّر قراءة PLAYLIST_LIMIT من DB، سيُستخدم الافتراضي ({config.PLAYLIST_LIMIT}): {_e}")
                    playlist_limit = config.PLAYLIST_LIMIT
                tracks = await yt.playlist(
                    playlist_limit, mention, url
                )
            except Exception as e:
                await safe_edit(
                    sent,
                    f"<blockquote>❌ فشل جلب قائمة التشغيل.\n\n"
                    f"قوائم تشغيل يوتيوب تواجه مشاكل حالياً. "
                    f"يرجى محاولة تشغيل الأغاني بشكل فردي بدلاً من ذلك.</blockquote>"
                )
                return

            if not tracks:
                await safe_edit(sent, m.lang["playlist_error"])
                return

            file = tracks[0]
            tracks.remove(file)
            file.message_id = sent.id
        else:
            file = await yt.search(url, sent.id)

        if not file:
            await safe_edit(
                sent,
                m.lang["play_not_found"].format(config.SUPPORT_CHAT)
            )
            return

    elif len(m.command) >= 2:
        query = " ".join(m.command[1:])
        file = await yt.search(query, sent.id)
        if not file:
            await safe_edit(
                sent,
                m.lang["play_not_found"].format(config.SUPPORT_CHAT)
            )
            return

    if not file:
        return

    file.video = getattr(file, "video", False) or video
    if file.video:
        for track in tracks:
            track.video = True

    # Skip duration check for live streams
    try:
        duration_limit = await db.get_setting("DURATION_LIMIT", config.DURATION_LIMIT)
    except Exception as _e:
        logger.warning(f"[settings] تعذّر قراءة DURATION_LIMIT من DB، سيُستخدم الافتراضي ({config.DURATION_LIMIT}): {_e}")
        duration_limit = config.DURATION_LIMIT
    if not file.is_live and file.duration_sec > duration_limit:
        await safe_edit(
            sent,
            m.lang["play_duration_limit"].format(duration_limit // 60)
        )
        return

    if await db.is_logger():
        await utils.play_log(m, file.title, file.duration)

    file.user = mention
    if force:
        queue.force_add(chat_id, file)
    else:
        position = queue.add(chat_id, file)  # Returns 0-based index

        if await db.get_call(chat_id):
            # When call is active, position 0 is currently playing
            # So actual waiting position is: position (e.g., 1st waiting = index 1)
            # Display as 1-based for users: index 1 → "1st in queue"
            await safe_edit(
                sent,
                m.lang["play_queued"].format(
                    position,  # Shows waiting position: 1, 2, 3...
                    file.url,
                    file.title,
                    file.duration,
                    m.from_user.mention,
                ),
                reply_markup=buttons.play_queued(
                    chat_id, file.id, m.lang["play_now"]
                ),
            )
            if tracks:
                added = playlist_to_queue(chat_id, tracks)
                try:
                    await app.send_message(
                        chat_id=m.chat.id,
                        text=m.lang["playlist_queued"].format(len(tracks)) + added,
                    )
                except Exception:
                    # Can't send message, continue anyway
                    pass
            
            # ✨ NEW: Start preloading queued tracks in background
            try:
                from UltraMusic import preload
                asyncio.create_task(preload.start_preload(chat_id, count=2))
            except Exception:
                # Non-critical, continue without preload
                pass
            
            return

    if not file.file_path:
        file.file_path = await yt.download(
            file.id,
            is_live=file.is_live,
            video=getattr(file, "video", False),
        )
        if not file.file_path:
            await safe_edit(
                sent,
                "<blockquote>❌ فشل تنزيل الوسائط.\n\n"
                "الأسباب المحتملة:\n"
                "• اكتشف يوتيوب نشاط بوت (حدّث الكوكيز)\n"
                "• الفيديو محظور إقليمياً أو خاص\n"
                "• محتوى مقيّد بالعمر (يتطلب كوكيز)</blockquote>"
            )
            return

    try:
        # Pass message_chat_id only if it's different from chat_id (channel play mode)
        await tune.play_media(
            chat_id=chat_id, 
            message=sent, 
            media=file, 
            message_chat_id=message_chat_id if chat_id != message_chat_id else None
        )
        # Track this play for the top-tracks statistics (global / per-chat / per-user)
        asyncio.create_task(db.increment_track(
            file.id, chat_id, m.from_user.id, file.title
        ))
        # React with emoji on successful play
        try:
            emoji_str = m.lang["play_emoji"]
            try:
                reaction = ReactionEmoji(emoji_str) if ReactionEmoji is not None else emoji_str
            except (ValueError, TypeError):
                reaction = emoji_str
            await m.react(reaction)
        except Exception:
            # If reaction fails, continue anyway (not critical)
            pass
    except Exception as e:
        error_msg = str(e)
        if "bot" in error_msg.lower() or "sign in" in error_msg.lower():
            await safe_edit(
                sent,
                "<blockquote>❌ تم تفعيل كشف البوت من يوتيوب.\n\n"
                "الحل:\n"
                "• حدّث كوكيز يوتيوب في مجلد `UltraMusic/cookies/`\n"
                "• انتظر بضع دقائق قبل المحاولة مجدداً\n\n"
                f"الدعم: {config.SUPPORT_CHAT}</blockquote>"
            )
        else:
            await safe_edit(
                sent,
                f"<blockquote>❌ خطأ في التشغيل:\n{error_msg}\n\n"
                f"الدعم: {config.SUPPORT_CHAT}</blockquote>"
            )
        return
    if not tracks:
        return
    added = playlist_to_queue(chat_id, tracks)
    try:
        await app.send_message(
            chat_id=m.chat.id,
            text=m.lang["playlist_queued"].format(len(tracks)) + added,
        )
    except Exception:
        # Can't send message, but playback is working
        pass
