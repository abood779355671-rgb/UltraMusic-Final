# ==============================================================================
# config.py - Bot Configuration Manager
# ==============================================================================
# This file loads all configuration settings from environment variables (.env file).
#
# What it does:
# - Reads settings from .env file (API keys, bot token, database URL, etc.)
# - Validates that all required settings are present
# - Provides default values for optional settings
# - Converts string values to appropriate types (int, bool, list)
#
# Important: Never commit your .env file to git! It contains sensitive data.
# Use sample.env as a template to create your own .env file.
# ==============================================================================

"""
Configuration module for ˹ᴜʟᴛʀᴀ ᴍᴜꜱɪᴄ˼.

This module loads and validates all environment variables required for the bot to function.
It provides a centralized Config class that manages all configuration settings.
"""

from os import getenv
from typing import List
from dotenv import load_dotenv

# Load environment variables from .env file (create one from sample.env)
load_dotenv()


class Config:
    """
    Configuration class for managing bot settings.

    All settings are loaded from environment variables with sensible defaults where applicable.
    Required variables are validated on initialization through the check() method.
    """

    def __init__(self):
        """Initialize configuration by loading all environment variables."""

        # ============ TELEGRAM API CREDENTIALS ============
        # Get these from https://my.telegram.org
        # Telegram API ID (numeric)
        self.API_ID: int = int(getenv("API_ID", "0"))
        # Telegram API Hash (hexadecimal)
        self.API_HASH: str = getenv("API_HASH", "")

        # ============ BOT CONFIGURATION ============
        # Bot token from @BotFather
        self.BOT_TOKEN: str = getenv("BOT_TOKEN", "")
        # Group/channel ID for logs (must be negative)
        self.LOGGER_ID: int = int(getenv("LOGGER_ID", "0"))
        # Your user ID (get from @userinfobot)
        self.OWNER_ID: int = int(getenv("OWNER_ID", "0"))
        # Optional: owner username (without @) used for the "تواصل مع المطور" button.
        # If left empty, the button falls back to OWNER_ID via tg://user?id=...
        self.OWNER_USERNAME: str = getenv("OWNER_USERNAME", "").lstrip("@")

        # ============ DATABASE CONFIGURATION ============
        # MongoDB connection URL (mongodb+srv://...)
        self.MONGO_URL: str = getenv("MONGO_DB_URI", "")

        # ============ MUSIC BOT LIMITS ============
        # Convert minutes to seconds for duration limit
        # Max song duration (default: 300 min)
        self.DURATION_LIMIT: int = int(getenv("DURATION_LIMIT", "300")) * 60
        # Max songs in queue (default: 30)
        self.QUEUE_LIMIT: int = int(getenv("QUEUE_LIMIT", "30"))
        # Max songs from playlist (default: 20)
        self.PLAYLIST_LIMIT: int = int(getenv("PLAYLIST_LIMIT", "20"))
        # Max duration (in seconds) for the /بحث download-and-send command.
        # Separate from DURATION_LIMIT because this limit applies to a full
        # file download+upload, not voice-chat streaming. Default: 20 minutes.
        self.SONG_DOWNLOAD_LIMIT: int = int(getenv("SONG_DOWNLOAD_LIMIT", "20")) * 60

        # ============ ASSISTANT/USERBOT SESSIONS ============
        # Pyrogram session strings - get from @StringFatherBot
        # You can have up to 3 assistants for handling multiple groups
        # Primary assistant (required)
        self.SESSION1: str = getenv("STRING_SESSION", "")
        # Secondary assistant (optional)
        self.SESSION2: str = getenv("STRING_SESSION2", "")
        # Tertiary assistant (optional)
        self.SESSION3: str = getenv("STRING_SESSION3", "")

        # ============ SUPPORT LINKS ============
        self.SUPPORT_CHANNEL: str = getenv(
            "SUPPORT_CHANNEL", "https://t.me/C44PP")
        self.SUPPORT_CHAT: str = getenv("SUPPORT_CHAT", "https://t.me/C44PP")

        # ============ EXCLUDED CHATS ============
        # Parse comma-separated chat IDs that assistants should never leave
        self.EXCLUDED_CHATS: List[int] = self._parse_excluded_chats()

        # ============ FEATURE FLAGS ============
        # Auto-end stream when queue is empty
        self.AUTO_END: bool = self._str_to_bool(getenv("AUTO_END", "False"))
        # Auto-leave inactive chats
        self.AUTO_LEAVE: bool = self._str_to_bool(getenv("AUTO_LEAVE", "False"))
        # Enable/disable thumbnail generation (set False to use default thumb)
        self.THUMB_GEN: bool = self._str_to_bool(getenv("THUMB_GEN", "True"))
        # Enable/disable video playback commands (/vplay)
        self.VIDEO_PLAY: bool = self._str_to_bool(getenv("VIDEO_PLAY", "False"))
        # Maximum video height (in pixels) when downloading /vplay media
        self.VIDEO_MAX_HEIGHT: int = self._parse_video_height()

        # ============ YOUTUBE COOKIES ============
        # Parse space-separated cookie URLs for age-restricted content
        self.COOKIES_URL: List[str] = self._parse_cookies()
        # How often (in hours) to automatically re-download cookies in the
        # background, since YouTube cookies expire periodically and the bot
        # would otherwise keep failing until manually redeployed.
        self.COOKIE_REFRESH_HOURS: float = float(getenv("COOKIE_REFRESH_HOURS", "6"))

        # ============ DOWNLOADS CLEANUP ============
        # Files in downloads/ that have not been accessed/modified for this
        # many hours will be deleted by the periodic cleanup task.
        # Set to 0 to disable automatic cleanup entirely.
        self.DOWNLOADS_CLEANUP_HOURS: int = int(getenv("DOWNLOADS_CLEANUP_HOURS", "48"))

        # ============ PLAY RATE LIMITING ============
        # Set RATE_LIMIT_ENABLED=False to disable entirely.
        # RATE_LIMIT_MAX  : max play requests per user per chat inside the window.
        # RATE_LIMIT_WINDOW: sliding window size in seconds.
        self.RATE_LIMIT_ENABLED: bool = self._str_to_bool(getenv("RATE_LIMIT_ENABLED", "True"))
        self.RATE_LIMIT_MAX: int = int(getenv("RATE_LIMIT_MAX", "5"))
        self.RATE_LIMIT_WINDOW: int = int(getenv("RATE_LIMIT_WINDOW", "30"))

        # ============ API FAILURE ALERTS ============
        # Set API_ALERT_ENABLED=False to silence LOGGER_ID alerts.
        # API_ALERT_THRESHOLD : consecutive failures before alerting.
        # API_ALERT_COOLDOWN  : minimum seconds between two alerts (default 1 h).
        self.API_ALERT_ENABLED: bool   = self._str_to_bool(getenv("API_ALERT_ENABLED", "True"))
        self.API_ALERT_THRESHOLD: int  = int(getenv("API_ALERT_THRESHOLD", "5"))
        self.API_ALERT_COOLDOWN: float = float(getenv("API_ALERT_COOLDOWN", "3600"))

        # ============ HEALTH CHECK ============
        # Set HEALTH_CHECK_ENABLED=False to disable the periodic health log.
        # HEALTH_CHECK_INTERVAL: how often to run the check, in seconds (default 15 min).
        self.HEALTH_CHECK_ENABLED: bool = self._str_to_bool(getenv("HEALTH_CHECK_ENABLED", "True"))
        self.HEALTH_CHECK_INTERVAL: int = int(getenv("HEALTH_CHECK_INTERVAL", "900"))

        # ============ PO TOKEN PROVIDER (bgutil-ytdlp-pot-provider) ============
        # Base URL of the separately-running bgutil-ytdlp-pot-provider HTTP
        # server (e.g. "http://1.2.3.4:4416" on your Lightsail VPS, or a
        # Render service URL). Leave empty to disable PO token usage.
        self.POT_PROVIDER_URL: str = getenv("POT_PROVIDER_URL", "").strip()
        # Residential/datacenter proxy for yt-dlp (e.g. Webshare, Brightdata, Oxylabs)
        # Format: http://user:pass@host:port
        self.YTDLP_PROXY: str = getenv("YTDLP_PROXY", "").strip()

        # ============ EXTERNAL DOWNLOAD API (ArtistBots) ============
        # Wired specifically for the ArtistBots API. The bot calls:
        #   GET {API_URL}/download?url={video_id}&type=audio&api_key={key}
        #   GET {VIDEO_API_URL}/download?url={video_id}&type=video&api_key={key}
        # and streams the binary response straight to disk.
        # - API_URL      : ArtistBots base URL for audio   (e.g. https://artistbots.onrender.com)
        # - VIDEO_API_URL: ArtistBots base URL for video   (usually the same host as API_URL)
        # - API_KEYS     : one or more ArtistBots API keys, comma-separated
        #                  (e.g. "key1,key2,key3"). Each key has its own
        #                  500/day quota, so N keys ≈ N × 500 requests/day.
        #                  The bot rotates to the next key on every request
        #                  (round-robin), regardless of success/failure.
        # - API_KEY      : single-key fallback (kept for backwards
        #                  compatibility). Used only if API_KEYS is empty.
        # Leave URL/keys empty to disable (no fallback — see youtube.py).
        self.API_URL: str = getenv("API_URL", "").strip()
        self.VIDEO_API_URL: str = getenv("VIDEO_API_URL", "").strip()
        self.API_KEY: str = getenv("API_KEY", "").strip()
        self.API_KEYS: List[str] = self._parse_api_keys()
        # Timeout in seconds for streaming ArtistBots API response to disk (default 5 min)
        self.API_STREAM_TIMEOUT: int = int(getenv("API_STREAM_TIMEOUT", "300"))

        # ============ IMAGE URLS ============
        # URLs for various bot images
        self.DEFAULT_THUMB: str = getenv(
            "DEFAULT_THUMB",
            "https://i.postimg.cc/Vsxjn68j/IMG-20260621-230357-390.jpg"  # Default thumbnail
        )
        self.PING_IMG: str = getenv(
            "PING_IMG", "https://i.postimg.cc/Vsxjn68j/IMG-20260621-230357-390.jpg")    # Ping command image
        self.START_IMG: str = getenv(
            "START_IMG", "https://i.postimg.cc/FsmSWSkn/IMG-20260621-215549-468.jpg")  # Start command image
        self.RADIO_IMG: str = getenv(
            "RADIO_IMG", "https://files.catbox.moe/t03fzk.png")    # Radio command image

        # ============ MODERATION ============
        # List of usernames to exclude from admin mentions
        self.EXCLUDED_USERNAMES: List[str] = getenv("EXCLUDED_USERNAMES", "").split()

    def _parse_video_height(self) -> int:
        """Clamp configured video height to a safe HD range."""
        default_height = 1080
        raw_value = getenv("VIDEO_MAX_HEIGHT", str(default_height))
        try:
            height = int(raw_value)
        except (TypeError, ValueError):
            return default_height

        # Allow disabling the cap by setting to 0 or negative (interpreted as unlimited)
        if height <= 0:
            return 0

        # Clamp between 480p and 2160p to avoid unrealistic requests
        return max(480, min(height, 2160))

    def _parse_excluded_chats(self) -> List[int]:
        """
        Parse excluded chat IDs from comma-separated string.

        Returns:
            List[int]: List of chat IDs to exclude from auto-leave.
        """
        excluded = getenv("EXCLUDED_CHATS", "")
        if not excluded:
            return []

        chat_ids = []
        for chat_id in excluded.split(","):
            chat_id = chat_id.strip()
            if chat_id.lstrip('-').isdigit():
                chat_ids.append(int(chat_id))
        return chat_ids

    def _parse_cookies(self) -> List[str]:
        """
        Parse YouTube cookie URLs from space-separated string.
        Supports multiple cookie sources (batbin, pastebin, etc.)

        Returns:
            List[str]: List of valid cookie URLs.
        """
        cookie_str = getenv("COOKIE_URL", "")
        if not cookie_str:
            return []

        valid_sources = ["batbin.me", "pastebin.com", "paste.ee", "rentry.co"]
        return [
            url.strip()
            for url in cookie_str.split()
            if url.strip() and any(source in url for source in valid_sources)
        ]

    def _parse_api_keys(self) -> List[str]:
        """
        Parse one or more ArtistBots API keys.

        Reads API_KEYS (comma-separated, e.g. "key1,key2,key3") and falls
        back to the single API_KEY if API_KEYS is empty. Each key carries
        its own daily quota on ArtistBots' side, so N keys roughly multiply
        the bot's effective daily request budget.

        Returns:
            List[str]: Ordered list of unique, non-empty API keys.
        """
        raw = getenv("API_KEYS", "").strip()
        keys = [k.strip() for k in raw.split(",") if k.strip()] if raw else []

        if not keys:
            single = getenv("API_KEY", "").strip()
            if single:
                keys = [single]

        # De-duplicate while preserving order
        seen = set()
        unique_keys = []
        for k in keys:
            if k not in seen:
                seen.add(k)
                unique_keys.append(k)
        return unique_keys

    @staticmethod
    def _str_to_bool(value: str) -> bool:
        """
        Convert string to boolean value.

        Args:
            value: String representation of boolean.

        Returns:
            bool: Converted boolean value.
        """
        return value.lower() in ("true", "1", "yes", "y", "on")

    def check(self) -> None:
        """
        Validate that all required environment variables are set.

        Raises:
            SystemExit: If any required variables are missing.
        """
        required_vars = {
            "API_ID": self.API_ID,
            "API_HASH": self.API_HASH,
            "BOT_TOKEN": self.BOT_TOKEN,
            "MONGO_DB_URI": self.MONGO_URL,
            "LOGGER_ID": self.LOGGER_ID,
            "OWNER_ID": self.OWNER_ID,
            "STRING_SESSION": self.SESSION1,
        }

        missing = [
            name for name, value in required_vars.items()
            if not value or (isinstance(value, int) and value == 0)
        ]

        if missing:
            raise SystemExit(
                f"❌ Missing required environment variables: {', '.join(missing)}\n"
                f"Please check your .env file and ensure all required variables are set."
            )


# ============ NEW FEATURE SETTINGS ============
# Audio bitrate for downloads (64k / 128k / 192k / 320k)
AUDIO_BITRATE = "128k"
# Video quality for downloads (480 / 720 / 1080)
VIDEO_QUALITY = "720"
# Clean mode: auto-delete Now Playing message after track ends
CLEANMODE = False
# Enable top tracks tracking
TOP_TRACKS = True
