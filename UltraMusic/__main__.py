# ==============================================================================
# __main__.py - Main Entry Point for ˹ᴜʟᴛʀᴀ ᴍᴜꜱɪᴄ˼
# ==============================================================================
# This is the main file that starts the bot. It performs the following:
# 1. Connects to the database
# 2. Starts the bot client
# 3. Starts assistant (userbot) clients
# 4. Loads all plugin modules
# 5. Initializes YouTube cookies if configured
# 6. Keeps the bot running until manually stopped
# ==============================================================================

import asyncio
import importlib
import sys
import time as _time
from pathlib import Path

from pyrogram import idle

# Raise the file descriptor limit on Linux to avoid "[Errno 24] Too many open files"
# when serving many groups concurrently (each audio stream + ffmpeg probe opens FDs).
if sys.platform != "win32":
    try:
        import resource
        _soft, _hard = resource.getrlimit(resource.RLIMIT_NOFILE)
        _target = min(65536, _hard)
        if _soft < _target:
            resource.setrlimit(resource.RLIMIT_NOFILE, (_target, _hard))
    except Exception:
        pass

from UltraMusic import (tune, app, config, db,
                   logger, stop, userbot, yt, queue)
from UltraMusic.plugins import all_modules


async def _downloads_cleanup_loop() -> None:
    """Periodic task: delete stale files from the downloads/ directory.

    Runs every 6 hours and removes any file whose last-modified time (mtime)
    is older than ``config.DOWNLOADS_CLEANUP_HOURS`` hours.  Set that variable
    to 0 in .env to disable cleanup entirely.

    Why mtime?  yt-dlp and the ArtistBots API both update mtime when a file
    is (re-)written, so a recently streamed file that was cached from a prior
    session keeps its mtime fresh – only truly forgotten files get deleted.
    """
    cleanup_hours = config.DOWNLOADS_CLEANUP_HOURS
    if not cleanup_hours:
        logger.info("🧹 Downloads cleanup disabled (DOWNLOADS_CLEANUP_HOURS=0).")
        return

    logger.info(
        f"🧹 Downloads cleanup task started "
        f"(interval=6h, max_age={cleanup_hours}h)."
    )
    while True:
        await asyncio.sleep(6 * 3600)  # wait 6 hours between sweeps
        try:
            cutoff = _time.time() - cleanup_hours * 3600
            dl_dir = Path("downloads")
            if not dl_dir.exists():
                continue

            deleted = errors = skipped = 0
            for f in dl_dir.iterdir():
                if not f.is_file():
                    continue
                # Skip files still being written (.part / .ytdl temp files)
                if f.suffix.lower() in (".part", ".ytdl", ".temp"):
                    skipped += 1
                    continue
                try:
                    if f.stat().st_mtime < cutoff:
                        f.unlink()
                        deleted += 1
                except OSError:
                    errors += 1

            logger.info(
                f"🧹 Downloads cleanup: deleted={deleted}, "
                f"skipped_temp={skipped}, errors={errors}."
            )
        except Exception as e:
            logger.error(f"Downloads cleanup sweep failed: {e}")


async def _health_check_loop() -> None:
    """Periodic health check — logs a one-line summary every HEALTH_CHECK_INTERVAL seconds.

    Checks:
    - MongoDB round-trip latency (ping command)
    - Number of PyTgCalls assistant clients started
    - Number of currently active voice-chat sessions

    Nothing is sent to any chat; output goes to the log file only (INFO level).
    Set HEALTH_CHECK_ENABLED=False in .env to disable entirely.
    """
    if not config.HEALTH_CHECK_ENABLED:
        logger.info("💓 Health check disabled (HEALTH_CHECK_ENABLED=False).")
        return

    interval = max(config.HEALTH_CHECK_INTERVAL, 60)  # minimum 60 s
    logger.info(f"💓 Health check task started (interval={interval}s).")

    while True:
        await asyncio.sleep(interval)
        try:
            # 1. DB ping
            t0 = _time.monotonic()
            await db.mongo.admin.command("ping")
            db_ms = round((_time.monotonic() - t0) * 1000)

            # 2. Active PyTgCalls assistant count
            assistants = len(tune.clients)

            # 3. Active voice-chat sessions (in-memory counter)
            active_calls = len(db.active_calls)

            # 4. Queue depth (total tracks queued across all groups)
            total_queued = sum(
                len(dq) for dq in queue.queues.values()
            )

            logger.info(
                f"💓 Health: db={db_ms}ms | "
                f"assistants={assistants} | "
                f"calls={active_calls} | "
                f"queued_tracks={total_queued}"
            )
        except Exception as e:
            logger.warning(f"💔 Health check error: {e}")


async def main():
    try:
        # Set asyncio exception handler now that the loop is running
        from UltraMusic import set_exception_handler
        set_exception_handler()

        # Step 1: Connect to MongoDB database
        await db.connect()
        
        # Step 2: Start the main bot client
        await app.boot()
        
        # Step 3: Start assistant/userbot clients (for joining voice chats)
        await userbot.boot()
        
        # Step 4: Initialize voice call handler
        await tune.boot()

        # Step 5: Load all plugin modules (commands like /play, /pause, etc.)
        for module in all_modules:
            try:
                importlib.import_module(f"UltraMusic.plugins.{module}")
            except Exception as e:
                logger.error(f"Failed to load plugin {module}: {e}", exc_info=True)
        logger.info(f"🔌 Loaded {len(all_modules)} plugin modules.")

        # Step 6: Download YouTube cookies if URLs are provided (for age-restricted videos)
        if config.COOKIES_URL:
            try:
                await yt.save_cookies(config.COOKIES_URL)
            except Exception as e:
                logger.error(f"Failed to download cookies: {e}")

            # Keep cookies fresh automatically in the background instead of
            # only fetching them once at startup. Without this, expired
            # cookies silently break /play until someone manually redeploys.
            asyncio.create_task(
                yt.start_cookie_auto_refresh(config.COOKIES_URL, config.COOKIE_REFRESH_HOURS)
            )
            logger.info(f"🔄 Cookie auto-refresh scheduled every {config.COOKIE_REFRESH_HOURS}h.")

        # Step 7.5: Start background maintenance tasks
        from UltraMusic import tasks, queue as _queue
        tasks.append(asyncio.create_task(_downloads_cleanup_loop()))
        tasks.append(asyncio.create_task(_health_check_loop()))

        # Step 7: Load sudo users and blacklisted users from database
        sudoers = await db.get_sudoers()
        app.sudoers.update(sudoers)  # Add sudo users to set
        app.sudo_filter.update(sudoers)  # Add sudo users to filter
        app.bl_users.update(await db.get_blacklisted())  # Add blacklisted users to filter
        logger.info(f"👑 Loaded {len(app.sudoers)} sudo users.")
        logger.info("\n🎉 Bot started successfully! Ready to play music! 🎵\n")

        # Step 8: Keep the bot running (press Ctrl+C to stop)
        try:
            await idle()
        except KeyboardInterrupt:
            logger.info("Received stop signal...")
        except Exception as e:
            logger.error(f"Error during idle: {e}", exc_info=True)
        
        # Step 9: Cleanup and shutdown when bot is stopped
        await stop()
    except Exception as e:
        logger.error(f"Critical error in main: {e}", exc_info=True)
        raise


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Bot stopped by user (Ctrl+C)")
    except SystemExit as e:
        logger.error(f"Bot exited with system error: {e}")
        raise
    except Exception as e:
        logger.error(f"Unexpected error caused bot to stop: {e}", exc_info=True)
        # Don't raise - allow clean shutdown
