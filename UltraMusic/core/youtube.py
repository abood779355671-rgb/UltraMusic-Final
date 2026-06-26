# ==============================================================================
# youtube.py - YouTube Download & Search Handler
# ==============================================================================
# This file handles all YouTube-related operations:
# - Searching for videos/audio
# - Downloading YouTube content using yt-dlp
# - Managing YouTube cookies for age-restricted content
# - Caching search results for better performance
# - Validating YouTube URLs
# - External API fallback when yt-dlp is blocked (API_URL / VIDEO_API_URL)
# ==============================================================================

import contextlib
import os
import re
import glob
import time
import yt_dlp
import random
import asyncio
import aiohttp
import hashlib
from dataclasses import replace
from pathlib import Path
from typing import Optional, Union

from pyrogram import enums, types
from ytlookup import Playlist, videosearch
from UltraMusic import config, logger
from UltraMusic.helpers import Track, utils

# ── External API config ────────────────────────────────────────────────────────
_YOUTUBE_ID_RE = re.compile(r"^[a-zA-Z0-9_-]{11}$")
_CHUNK_SIZE = 128 * 1024  # 128 KB per chunk when streaming API response to disk


def _extract_video_id(link: str) -> str:
    """Return the bare 11-char YouTube video ID from a URL or bare ID."""
    if not link:
        return ""
    s = link.strip()
    if _YOUTUBE_ID_RE.match(s):
        return s
    if "v=" in s:
        return s.split("v=")[-1].split("&")[0]
    last = s.split("/")[-1].split("?")[0]
    if _YOUTUBE_ID_RE.match(last):
        return last
    return ""


class YouTube:
    def __init__(self):
        """Initialize YouTube handler with configuration and caching."""
        self.base = "https://www.youtube.com/watch?v="  # Base YouTube URL
        self.cookies = []  # List of available cookie files
        self.checked = False  # Whether cookies directory has been checked
        self.warned = False  # Whether missing cookies warning has been shown

        # ── External API flags (evaluated lazily so config is fully loaded) ──
        self._api_session: Optional[aiohttp.ClientSession] = None
        self._api_session_lock = asyncio.Lock()

        # ── ArtistBots key rotation (round-robin across config.API_KEYS) ─────
        self._api_key_index = 0
        self._api_key_lock = asyncio.Lock()

        # ── ArtistBots per-key statistics (in-memory, reset on restart) ──────
        # Structure: { api_key: {"ok": int, "fail": int, "last_fail": float|None} }
        self._api_stats: dict = {}
        # Consecutive failure counter (across all keys, for alert threshold).
        # Reset to 0 on any successful download.
        self._api_consec_fails: int = 0
        # Wall-clock time of the last "all keys failing" alert sent to LOGGER_ID.
        self._api_last_alert: float = 0.0

        # Regular expression to match YouTube URLs (videos, shorts, live, playlists)
        self.regex = re.compile(
            r"(https?://)?(www\.|m\.|music\.)?"
            r"(youtube\.com/(watch\?v=|shorts/|live/|embed/|playlist\?list=)|youtu\.be/)"
            r"([A-Za-z0-9_-]{11}|PL[A-Za-z0-9_-]+)([&?][^\s]*)?"
        )

        # Cache search results to reduce API calls (10 minute TTL)
        self.search_cache = {}  # {"query_video": (result, timestamp)}

        # **PERFORMANCE FIX**: Limit concurrent downloads to prevent bandwidth saturation
        # With 15-20 groups, unlimited concurrent downloads cause 320+ connections
        self._download_semaphore = asyncio.Semaphore(5)  # Max 5 simultaneous downloads
        self._max_video_height = getattr(config, "VIDEO_MAX_HEIGHT", 1080)

    def _locate_download_file(self, video_id: str, video: bool = False) -> Optional[str]:
        """Locate any completed download file for a video id."""
        pattern = f"downloads/{video_id}*"
        candidates = sorted([
            path for path in glob.glob(pattern)
            if not path.endswith((".part", ".ytdl", ".info.json", ".temp"))
        ])

        video_exts = {".mp4", ".mkv", ".webm", ".mov"}
        audio_exts = {".m4a", ".webm", ".opus", ".mp3", ".ogg", ".wav", ".flac"}

        if video:
            for path in candidates:
                if os.path.isdir(path):
                    continue
                if Path(path).suffix.lower() in video_exts:
                    return path
        else:
            for path in candidates:
                if os.path.isdir(path):
                    continue
                if Path(path).suffix.lower() in audio_exts:
                    return path

        for path in candidates:
            if os.path.isdir(path):
                continue
            return path
        return None

    # ── External API helpers ───────────────────────────────────────────────────

    def _use_audio_api(self) -> bool:
        return bool(config.API_URL and config.API_KEYS)

    def _use_video_api(self) -> bool:
        return bool(config.VIDEO_API_URL and config.API_KEYS)

    async def _next_api_key(self) -> Optional[str]:
        """
        Return the next ArtistBots API key in round-robin order.

        Each call advances the rotation by one, regardless of whether the
        previous request succeeded or failed — every request gets a turn
        with the next key, spreading load evenly so N keys give roughly
        N × 500/day of effective quota instead of hammering one key.
        """
        keys = config.API_KEYS
        if not keys:
            return None
        async with self._api_key_lock:
            key = keys[self._api_key_index % len(keys)]
            self._api_key_index = (self._api_key_index + 1) % len(keys)
            return key

    async def _get_api_session(self) -> aiohttp.ClientSession:
        """Return a shared aiohttp session for API calls (created on demand)."""
        if self._api_session and not self._api_session.closed:
            return self._api_session
        async with self._api_session_lock:
            if self._api_session and not self._api_session.closed:
                return self._api_session
            timeout = aiohttp.ClientTimeout(total=600, sock_connect=20, sock_read=60)
            connector = aiohttp.TCPConnector(limit=0, ttl_dns_cache=300, enable_cleanup_closed=True)
            self._api_session = aiohttp.ClientSession(timeout=timeout, connector=connector)
            return self._api_session

    async def _artistbots_download(self, vid: str, base_url: str, video: bool) -> Optional[str]:
        """
        Download audio/video directly via the ArtistBots API.

        Calls GET {base_url}/download?url={vid}&type={audio|video}&api_key={key}
        and streams the binary response straight to disk (no polling — the
        ArtistBots endpoint returns the file body directly). The api_key is
        picked round-robin from config.API_KEYS so multiple keys share load
        evenly (each key has its own 500/day quota on ArtistBots' side).
        """
        api_key = await self._next_api_key()
        if not base_url or not api_key:
            return None

        download_type = "video" if video else "audio"
        file_ext = ".mp4" if video else ".mp3"
        out_path = f"downloads/{vid}{file_ext}"

        os.makedirs("downloads", exist_ok=True)
        if os.path.exists(out_path) and os.path.getsize(out_path) > 0:
            return out_path

        params = {
            "url": vid,
            "type": download_type,
            "api_key": api_key,
        }
        masked_key = api_key[:8] + "..." if len(api_key) > 8 else "***"

        try:
            session = await self._get_api_session()
            endpoint = f"{base_url.rstrip('/')}/download"
            logger.debug(f"Calling ArtistBots API with key {masked_key}: {endpoint}")
            async with session.get(
                endpoint,
                params=params,
                timeout=aiohttp.ClientTimeout(total=getattr(config, "API_STREAM_TIMEOUT", 300)),
            ) as resp:
                if resp.status != 200:
                    logger.warning(
                        f"⚠️ ArtistBots API returned status {resp.status} for {vid} (key {masked_key})"
                    )
                    self._record_api_result(api_key, success=False)
                    return None

                with open(out_path, "wb") as f:
                    async for chunk in resp.content.iter_chunked(_CHUNK_SIZE):
                        if not chunk:
                            break
                        f.write(chunk)

            if os.path.exists(out_path) and os.path.getsize(out_path) > 0:
                logger.info(
                    f"✅ {download_type.capitalize()} for {vid} downloaded via ArtistBots API (key {masked_key})"
                )
                self._record_api_result(api_key, success=True)
                return out_path

            if os.path.exists(out_path):
                os.remove(out_path)
            self._record_api_result(api_key, success=False)
            return None

        except asyncio.TimeoutError:
            logger.error(f"⏰ ArtistBots API timeout for {vid} (key {masked_key})")
            self._record_api_result(api_key, success=False)
            return None
        except aiohttp.ClientError as e:
            logger.error(f"🌐 ArtistBots API client error for {vid} (key {masked_key}): {e}")
            self._record_api_result(api_key, success=False)
            return None
        except Exception as e:
            logger.error(f"❌ ArtistBots API download failed for {vid}: {type(e).__name__}: {e}")
            self._record_api_result(api_key, success=False)
            return None

    async def _api_download_audio(self, link: str) -> Optional[str]:
        """Download audio via the ArtistBots API (uses API_URL + API_KEY)."""
        if not self._use_audio_api():
            return None
        vid = _extract_video_id(link)
        if not vid:
            return None
        return await self._artistbots_download(vid, config.API_URL, video=False)

    async def _api_download_video(self, link: str) -> Optional[str]:
        """Download video via the ArtistBots API (uses VIDEO_API_URL + API_KEY)."""
        if not self._use_video_api():
            return None
        vid = _extract_video_id(link)
        if not vid:
            return None
        return await self._artistbots_download(vid, config.VIDEO_API_URL, video=True)

    # ── API statistics helpers ─────────────────────────────────────────────────

    def _stats_for(self, api_key: str) -> dict:
        """Return (and lazily create) the stats bucket for *api_key*."""
        if api_key not in self._api_stats:
            self._api_stats[api_key] = {
                "ok": 0,
                "fail": 0,
                "last_fail": None,
                "last_used": None,  # وقت آخر استخدام (نجاح أو فشل)
            }
        return self._api_stats[api_key]

    def _record_api_result(self, api_key: str, success: bool) -> None:
        """Update per-key stats and the global consecutive-failure counter."""
        import time as _t
        bucket = self._stats_for(api_key)
        bucket["last_used"] = _t.time()          # ← تحديث وقت آخر استخدام دائماً
        if success:
            bucket["ok"] += 1
            self._api_consec_fails = 0          # any success resets the streak
        else:
            bucket["fail"] += 1
            bucket["last_fail"] = _t.time()
            self._api_consec_fails += 1
            # ── Feature 3: alert when streak crosses threshold ─────────────
            threshold = getattr(config, "API_ALERT_THRESHOLD", 5)
            cooldown  = getattr(config, "API_ALERT_COOLDOWN", 3600)
            if (
                getattr(config, "API_ALERT_ENABLED", True)
                and self._api_consec_fails >= threshold
                and _t.time() - self._api_last_alert > cooldown
            ):
                asyncio.create_task(self._send_api_alert())

    async def _send_api_alert(self) -> None:
        """Send a single flood-protected alert to LOGGER_ID when all keys fail."""
        import time as _t
        self._api_last_alert = _t.time()
        try:
            from UltraMusic import app
            total_ok   = sum(s["ok"]   for s in self._api_stats.values())
            total_fail = sum(s["fail"] for s in self._api_stats.values())
            keys_count = len(config.API_KEYS)
            await app.send_message(
                chat_id=config.LOGGER_ID,
                text=(
                    "⚠️ <b>تحذير: فشل API الخارجي</b>\n\n"
                    f"تجاوز عدد الفشل المتراكم <b>{self._api_consec_fails}</b> محاولة متتالية "
                    f"عبر <b>{keys_count}</b> مفتاح.\n"
                    f"إجمالي نجاح: <b>{total_ok}</b> | إجمالي فشل: <b>{total_fail}</b>\n\n"
                    "قد تكون الحصة اليومية لـ ArtistBots API قد نفدت.\n"
                    "سيتراجع البوت إلى yt-dlp تلقائياً."
                ),
            )
            logger.warning(
                f"📣 API alert sent to LOGGER_ID "
                f"(consec_fails={self._api_consec_fails}, keys={keys_count})."
            )
        except Exception as e:
            logger.error(f"Failed to send API alert: {e}")

    def get_api_stats(self) -> list[dict]:
        """Return per-key stats dicts (with masked keys) for the /apistatus command."""
        import time as _t
        rows = []
        for raw_key, bucket in self._api_stats.items():
            masked = raw_key[:8] + "..." if len(raw_key) > 8 else "***"
            ok, fail = bucket["ok"], bucket["fail"]
            total = ok + fail
            pct   = round(ok / total * 100) if total else 0
            last_fail_ts = bucket["last_fail"]
            if last_fail_ts:
                age_s = int(_t.time() - last_fail_ts)
                if age_s < 60:
                    last_fail_str = f"منذ {age_s} ث"
                elif age_s < 3600:
                    last_fail_str = f"منذ {age_s // 60} د"
                else:
                    last_fail_str = f"منذ {age_s // 3600} س"
            else:
                last_fail_str = "—"
            last_used_ts = bucket.get("last_used")
            if last_used_ts:
                age_s = int(_t.time() - last_used_ts)
                if age_s < 60:
                    last_used_str = f"منذ {age_s} ث"
                elif age_s < 3600:
                    last_used_str = f"منذ {age_s // 60} د"
                else:
                    last_used_str = f"منذ {age_s // 3600} س"
            else:
                last_used_str = "—"
            rows.append({
                "masked": masked,
                "ok": ok,
                "fail": fail,
                "pct": pct,
                "last_fail": last_fail_str,
                "last_used": last_used_str,
            })
        return rows

    # ── Cookie helpers ─────────────────────────────────────────────────────────

    def get_cookies(self):
        if not self.checked:
            try:
                self.cookies = [
                    f for f in os.listdir("UltraMusic/cookies")
                    if f.endswith(".txt")
                ]
            except FileNotFoundError:
                self.cookies = []
            self.checked = True

        if not self.cookies:
            try:
                self.cookies = [
                    f for f in os.listdir("UltraMusic/cookies")
                    if f.endswith(".txt")
                ]
            except FileNotFoundError:
                pass

        if not self.cookies:
            if not self.warned:
                self.warned = True
                logger.warning("Cookies are missing; downloads might fail.")
            return None
        return f"UltraMusic/cookies/{random.choice(self.cookies)}"

    async def save_cookies(self, urls: list[str]) -> None:
        """
        Download cookie files from the configured URLs.

        Uses a DETERMINISTIC filename per URL (hash of the URL) instead of a
        random one. This means refreshing the same COOKIE_URL later overwrites
        the same file on disk rather than piling up a new randomly-named file
        every time — which previously left old/expired cookie files on disk
        forever, still eligible to be picked by get_cookies()'s random.choice().
        """
        logger.info("🍪 Saving cookies from urls...")
        saved_count = 0
        fresh_filenames = []
        for url in urls:
            try:
                url_hash = hashlib.md5(url.encode()).hexdigest()[:10]
                path = f"UltraMusic/cookies/cookie_{url_hash}.txt"
                link = url.replace("me/", "me/raw/")
                async with aiohttp.ClientSession() as session:
                    timeout = aiohttp.ClientTimeout(total=20)
                    async with session.get(link, timeout=timeout) as resp:
                        if resp.status != 200:
                            logger.error(f"❌ Cookie download failed: HTTP {resp.status} from {url}")
                            continue
                        content = await resp.read()
                        if not content or len(content) < 50:
                            logger.error(f"❌ Cookie file empty or invalid from {url}")
                            continue
                        with open(path, "wb") as fw:
                            fw.write(content)
                        if os.path.exists(path) and os.path.getsize(path) > 0:
                            saved_count += 1
                            fresh_filenames.append(os.path.basename(path))
                            logger.info(f"✅ Saved: {os.path.basename(path)} ({len(content)} bytes)")
            except Exception as e:
                logger.error(f"❌ Cookie download error from {url}: {e}")

        if saved_count > 0:
            # Replace the active cookie list with exactly what was just
            # confirmed working. Any old/expired file that's no longer
            # returned this round stops being selectable immediately.
            self.cookies = fresh_filenames
            self.checked = True
            logger.info(f"✅ Cookies refreshed. ({saved_count} file(s) active)")
        elif self.cookies:
            logger.warning(
                "⚠️ Cookie refresh attempt failed (network/source issue) — "
                "keeping the previous working cookies as fallback."
            )
        else:
            self.checked = True
            logger.error("❌ No cookies saved! Check COOKIE_URL in .env. YouTube downloads will fail!")

    async def start_cookie_auto_refresh(self, urls: list[str], interval_hours: float = 6) -> None:
        """
        Background loop that re-downloads YouTube cookies on a fixed interval.

        Cookies were previously only fetched once at startup, so once they
        expired (YouTube cookies commonly go stale every few days) the bot
        kept failing until someone manually redeployed. This keeps them
        fresh automatically without restarting the bot.
        """
        if not urls:
            return
        interval_seconds = max(interval_hours, 0.5) * 3600
        while True:
            await asyncio.sleep(interval_seconds)
            logger.info("🔄 Auto-refreshing YouTube cookies...")
            try:
                await self.save_cookies(urls)
            except Exception as e:
                logger.error(f"❌ Scheduled cookie refresh failed: {e}")

    def valid(self, url: str) -> bool:
        return bool(re.match(self.regex, url))

    def url(self, message_1: types.Message) -> Union[str, None]:
        # Helper: extract the first URL from a single message (entities first,
        # then caption_entities). Returns the raw link string or None.
        def _extract_link(message: types.Message) -> Union[str, None]:
            text = message.text or message.caption or ""

            if message.entities:
                for entity in message.entities:
                    if entity.type == enums.MessageEntityType.URL:
                        return text[entity.offset: entity.offset + entity.length]

            if message.caption_entities:
                for entity in message.caption_entities:
                    if entity.type == enums.MessageEntityType.TEXT_LINK:
                        return entity.url

            return None

        # Priority 1: the message the user actually sent
        link = _extract_link(message_1)
        if link:
            return link.split("&si")[0].split("?si")[0]

        # Priority 2: the message being replied to (only if no link above)
        if message_1.reply_to_message:
            link = _extract_link(message_1.reply_to_message)
            if link:
                return link.split("&si")[0].split("?si")[0]

        return None

    def _build_ytdlp_search_opts(self, cookie: Optional[str] = None) -> dict:
        """Common yt-dlp options for search/metadata extraction with anti-bot bypass."""
        opts = {
            "quiet": True,
            "no_warnings": True,
            "noplaylist": True,
            "extract_flat": "in_playlist",
            "socket_timeout": 20,
            "extractor_retries": 3,
            # Android client bypasses YouTube bot-detection on datacenter IPs
            # (Lightsail, Render, etc.) without needing PO tokens.
            "extractor_args": {"youtube": {"player_client": ["android"]}},
        }
        if cookie:
            opts["cookiefile"] = cookie
        if getattr(config, "YTDLP_PROXY", ""):
            opts["proxy"] = config.YTDLP_PROXY
        if getattr(config, "POT_PROVIDER_URL", ""):
            opts["extractor_args"]["youtubepot-bgutilhttp"] = {
                "base_url": [config.POT_PROVIDER_URL]
            }
        return opts

    def _ytdlp_search_to_track(self, data: dict, m_id: int) -> Optional["Track"]:
        """Convert a yt-dlp info dict (from ytsearch or direct URL) to a Track."""
        if not data:
            return None
        vid = data.get("id")
        if not vid:
            return None
        duration_sec = data.get("duration")
        is_live = data.get("is_live", False)
        if duration_sec is None and is_live:
            duration = "LIVE"
            duration_sec = 0
        else:
            duration = utils.format_duration(int(duration_sec)) if duration_sec else "0:00"
        return Track(
            id=vid,
            channel_name=data.get("uploader") or data.get("channel", ""),
            duration=duration,
            duration_sec=int(duration_sec) if duration_sec else 0,
            message_id=m_id,
            title=(data.get("title") or "")[:25],
            thumbnail=data.get("thumbnail") or "",
            url=data.get("webpage_url") or f"https://www.youtube.com/watch?v={vid}",
            view_count=str(data.get("view_count", "")),
            is_live=is_live,
        )

    async def _ytdlp_text_search(self, query: str, m_id: int) -> Optional["Track"]:
        """
        Search YouTube using yt-dlp's ytsearch: prefix.
        Uses the Android client — works on Lightsail/datacenter IPs where
        py_yt (VideosSearch) is blocked.
        """
        def _search():
            cookie = self.get_cookies()
            opts = self._build_ytdlp_search_opts(cookie)
            opts["extract_flat"] = True
            with yt_dlp.YoutubeDL(opts) as ydl:
                return ydl.extract_info(f"ytsearch1:{query}", download=False)

        try:
            result = await asyncio.to_thread(_search)
            if not result:
                return None
            entries = result.get("entries") or []
            if not entries:
                return None
            return self._ytdlp_search_to_track(entries[0], m_id)
        except Exception as e:
            logger.warning(f"⚠️ yt-dlp text search failed for '{query}': {e}")
            return None

    async def search(self, query: str, m_id: int) -> Track | None:
        # Check cache first (10-minute TTL)
        cache_key = query
        current_time = asyncio.get_running_loop().time()

        if cache_key in self.search_cache:
            cached_result, cache_timestamp = self.search_cache[cache_key]
            if current_time - cache_timestamp < 600:  # 10 minutes
                # Return a fresh copy so downstream mutations don't leak back into cache
                fresh = replace(cached_result)
                fresh.message_id = m_id
                fresh.file_path = None
                fresh.user = None
                fresh.time = 0
                fresh.video = False
                return fresh

        try:
            if self.valid(query):
                # ── Direct URL / YouTube link ────────────────────────────────
                def _extract():
                    cookie = self.get_cookies()
                    opts = self._build_ytdlp_search_opts(cookie)
                    with yt_dlp.YoutubeDL(opts) as ydl:
                        return ydl.extract_info(query, download=False)

                data = await asyncio.to_thread(_extract)
                if not data:
                    return None

                track = self._ytdlp_search_to_track(data, m_id)
                if not track:
                    return None

            else:
                # ── Text search: try py_yt first, fall back to yt-dlp ────────
                # py_yt is faster but fails on datacenter IPs (Lightsail, etc.)
                # yt-dlp with android client works everywhere.
                track = None
                try:
                    import inspect
                    # ytlookup: بعض الإصدارات videosearch دالة async تُعيد النتائج مباشرة
                    # وإصدارات أخرى تُعيد كائناً يملك .next()
                    _vs = videosearch(query, limit=1)
                    if inspect.isawaitable(_vs):
                        # الإصدار الجديد: videosearch نفسها async
                        results = await asyncio.wait_for(_vs, timeout=8)
                    elif hasattr(_vs, "next"):
                        if inspect.iscoroutinefunction(_vs.next):
                            results = await asyncio.wait_for(_vs.next(), timeout=8)
                        else:
                            results = await asyncio.wait_for(
                                asyncio.to_thread(_vs.next), timeout=8
                            )
                    else:
                        results = None

                    if results and results.get("result"):
                        data = results["result"][0]
                        duration = data.get("duration")
                        is_live = duration is None or duration == "LIVE"
                        track = Track(
                            id=data.get("id"),
                            channel_name=data.get("channel", {}).get("name"),
                            duration=duration if not is_live else "LIVE",
                            duration_sec=0 if is_live else utils.to_seconds(duration),
                            message_id=m_id,
                            title=data.get("title", "")[:25],
                            thumbnail=data.get("thumbnails", [{}])[-1].get("url", "").split("?")[0],
                            url=data.get("link"),
                            view_count=data.get("viewCount", {}).get("short"),
                            is_live=is_live,
                        )
                except Exception as e:
                    logger.warning(f"⚠️ py_yt search blocked/failed ('{query}'): {e} — falling back to yt-dlp")

                # Fallback: yt-dlp ytsearch (works on Lightsail / datacenter IPs)
                if not track:
                    track = await self._ytdlp_text_search(query, m_id)

                if not track:
                    return None

            # Cache the result
            self.search_cache[cache_key] = (track, current_time)
            # Limit cache size to 100 entries
            if len(self.search_cache) > 100:
                oldest_key = min(self.search_cache.keys(),
                                 key=lambda k: self.search_cache[k][1])
                del self.search_cache[oldest_key]

            return replace(track)

        except Exception as e:
            logger.warning(f"⚠️ YouTube search failed for '{query}': {e}")
            return None

    async def playlist(self, limit: int, user: str, url: str) -> list[Track]:
        try:
            plist = await Playlist.get(url)
            tracks = []

            # Check if plist has videos
            if not plist or "videos" not in plist or not plist["videos"]:
                return []

            for data in plist["videos"][:limit]:
                try:
                    # Get thumbnail safely
                    thumbnails = data.get("thumbnails", [])
                    thumbnail_url = ""
                    if thumbnails and len(thumbnails) > 0:
                        thumbnail_url = thumbnails[-1].get(
                            "url", "").split("?")[0]

                    # Get link safely
                    link = data.get("link", "")
                    if "&list=" in link:
                        link = link.split("&list=")[0]

                    track = Track(
                        id=data.get("id", ""),
                        channel_name=data.get("channel", {}).get("name", ""),
                        duration=data.get("duration", "0:00"),
                        duration_sec=utils.to_seconds(
                            data.get("duration", "0:00")),
                        title=(data.get("title", "Unknown")[:25]),
                        thumbnail=thumbnail_url,
                        url=link,
                        user=user,
                        view_count="",
                    )
                    tracks.append(track)
                except Exception as e:
                    # Skip individual track errors
                    continue

            return tracks
        except KeyError as e:
            # Handle YouTube API structure changes
            raise Exception(
                f"Failed to parse playlist. YouTube may have changed their structure.")
        except Exception as e:
            # Re-raise other exceptions
            raise

    async def download(self, video_id: str, is_live: bool = False, video: bool = False) -> Optional[str]:
        url = self.base + video_id

        # For live streams, extract the direct stream URL using yt-dlp with cookies
        if is_live:
            cookie = self.get_cookies()
            ydl_opts = {
                "quiet": True,
                "no_warnings": True,
                "cookiefile": cookie,
                "format": "bestaudio/best",
                "noplaylist": True,
                "socket_timeout": 20,
                "extractor_retries": 5,
                "sleep_interval_requests": 1,
                # Use android client to bypass YouTube bot detection on server IPs
                "extractor_args": {"youtube": {"player_client": ["android"]}},
            }
            if getattr(config, "YTDLP_PROXY", ""):
                ydl_opts["proxy"] = config.YTDLP_PROXY
            if getattr(config, "POT_PROVIDER_URL", ""):
                ydl_opts["extractor_args"]["youtubepot-bgutilhttp"] = {
                    "base_url": [config.POT_PROVIDER_URL]
                }

            def _extract_url():
                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    try:
                        info = ydl.extract_info(url, download=False)
                        if not info:
                            return None

                        direct = info.get("url")
                        if direct:
                            return direct

                        # Some live extracts provide URLs only inside formats.
                        for fmt in info.get("formats", []):
                            if fmt.get("acodec") != "none" and fmt.get("url"):
                                return fmt["url"]

                        return info.get("manifest_url")
                    except yt_dlp.utils.ExtractorError as ex:
                        error_msg = str(ex)
                        if "not available" in error_msg.lower():
                            logger.error(
                                "Video format not available or region-blocked.")
                        else:
                            logger.error(
                                "Live stream URL extraction failed: %s", ex)
                        return None
                    except yt_dlp.utils.DownloadError as ex:
                        logger.error(
                            "Unexpected error during live stream extraction: %s", ex)
                        return None
                    except Exception as ex:
                        logger.error(
                            "Unexpected error during live stream extraction: %s", ex)
                        return None

            try:
                stream_url = await asyncio.wait_for(asyncio.to_thread(_extract_url), timeout=35)
            except asyncio.TimeoutError:
                logger.error("Live stream URL extraction timed out for %s", video_id)
                return None

            return stream_url

        # Download audio/video file
        # Don't hardcode extension - let yt-dlp choose best available format
        # Will use outtmpl pattern to get actual extension
        filename_pattern = f"downloads/{video_id}"
        
        # Check if any completed file for this video_id already exists
        existing_files = [
            f for f in glob.glob(f"{filename_pattern}.*")
            if not f.endswith('.part')
        ]
        if video:
            video_candidates = [
                f for f in existing_files
                if Path(f).suffix.lower() in {".mp4", ".mkv", ".webm", ".mov"}
            ]
            if video_candidates:
                return video_candidates[0]
        else:
            audio_candidates = [
                f for f in existing_files
                if Path(f).suffix.lower() in {".m4a", ".webm", ".opus", ".mp3", ".ogg", ".wav", ".flac"}
            ]
            if audio_candidates:
                return audio_candidates[0]

            # VPS caches are often dominated by mp4 due to prior /vplay usage.
            # Reuse those files for /play (audio-only mode) to avoid redundant redownloads.
            container_fallbacks = [
                f for f in existing_files
                if Path(f).suffix.lower() in {".mp4", ".mkv", ".mov"}
            ]
            if container_fallbacks:
                return container_fallbacks[0]
        
        # Ensure downloads directory exists with write permissions
        downloads_dir = Path("downloads")
        if not downloads_dir.exists():
            try:
                downloads_dir.mkdir(parents=True, exist_ok=True)
                logger.info("📁 Created downloads directory")
            except Exception as e:
                logger.error(f"❌ Cannot create downloads directory: {e}")
                return None

        # **PERFORMANCE FIX**: Use semaphore to limit concurrent downloads
        # Prevents bandwidth saturation when 15-20 groups download simultaneously
        async with self._download_semaphore:
            # ── ArtistBots API FIRST — yt-dlp fallback if API fails ─────────────
            url_for_api = self.base + video_id
            result = None

            if video and self._use_video_api():
                logger.info(f"🎯 Trying ArtistBots API for {video_id} (video)")
                result = await self._api_download_video(url_for_api)
            elif not video and self._use_audio_api():
                logger.info(f"🎯 Trying ArtistBots API for {video_id} (audio)")
                result = await self._api_download_audio(url_for_api)
            else:
                logger.error(
                    f"❌ ArtistBots API not configured (API_URL/VIDEO_API_URL/API_KEY missing) "
                    f"for {video_id} — falling back to yt-dlp."
                )

            if result:
                logger.info(f"✅ [SUCCESS] Downloaded via ArtistBots API: {video_id}")
                return result

            logger.error(f"❌ ArtistBots API failed for {video_id}. Attempting yt-dlp fallback...")

            # ── yt-dlp FALLBACK (when ArtistBots API is unavailable) ──────────────
            logger.warning(f"⚠️ Falling back to yt-dlp for {video_id}...")

            if video:
                ydl_opts = {
                    "format": f"bestvideo[height<={self._max_video_height}][ext=mp4]+bestaudio[ext=m4a]/best[height<={self._max_video_height}]",
                    "outtmpl": f"downloads/{video_id}.%(ext)s",
                    "quiet": True,
                    "no_warnings": True,
                    "noplaylist": True,
                    "merge_output_format": "mp4",
                    "socket_timeout": 30,
                    "extractor_retries": 3,
                    "extractor_args": {"youtube": {"player_client": ["android"]}},
                }
            else:
                ydl_opts = {
                    "format": "bestaudio[ext=m4a]/bestaudio/best",
                    "outtmpl": f"downloads/{video_id}.%(ext)s",
                    "quiet": True,
                    "no_warnings": True,
                    "noplaylist": True,
                    "socket_timeout": 30,
                    "extractor_retries": 3,
                    "extractor_args": {"youtube": {"player_client": ["android"]}},
                }

            cookie = self.get_cookies()
            if cookie:
                ydl_opts["cookiefile"] = cookie
            if getattr(config, "YTDLP_PROXY", ""):
                ydl_opts["proxy"] = config.YTDLP_PROXY

            def _ytdlp_download():
                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    ydl.download([url])

            try:
                await asyncio.wait_for(asyncio.to_thread(_ytdlp_download), timeout=120)
                fallback_result = self._locate_download_file(video_id, video)
                if fallback_result:
                    logger.info(f"✅ [FALLBACK] Downloaded via yt-dlp: {video_id}")
                    return fallback_result
                logger.error(f"❌ yt-dlp fallback: file not found after download for {video_id}")
                return None
            except asyncio.TimeoutError:
                logger.error(f"⏰ yt-dlp fallback timed out for {video_id}")
                return None
            except Exception as e:
                logger.error(f"❌ yt-dlp fallback failed for {video_id}: {e}")
                return None
