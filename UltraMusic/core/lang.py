# ==============================================================================
# lang.py - Multi-Language Support System
# ==============================================================================
# This file manages translations for the bot in multiple languages.
# - Translation files are stored in UltraMusic/locales/ as JSON files (en.json, ar.json)
# - Each chat can have its own language preference stored in MongoDB
#   (set via /language, persisted with db.set_lang / db.get_lang)
# - The @language() decorator automatically injects translations into message handlers
# - Default language is English ("en")
# - If a key is missing from the selected language, it automatically falls
#   back to English so the bot never crashes with a KeyError on an
#   untranslated string.
# ==============================================================================

import json
from functools import wraps
from pathlib import Path

from UltraMusic import db, logger

# Supported language codes and their display names.
# The bot is Arabic-only, so Arabic is the single supported language.
lang_codes = {
    "ar": "العربية",   # Arabic language
}

# The language we always fall back to when a key is missing or a chat
# has no language preference saved yet.
DEFAULT_LANG = "ar"


class FallbackDict(dict):
    """
    A dict that transparently falls back to the English translation
    whenever a key is missing in the currently selected language.

    This keeps every existing `m.lang["some_key"]` call site working
    exactly as before (plain dict access), while guaranteeing that an
    untranslated/missing key never raises a KeyError - it just silently
    returns the English string instead.
    """

    def __init__(self, data: dict, fallback: dict):
        super().__init__(data)
        self._fallback = fallback or {}

    def __missing__(self, key):
        if key in self._fallback:
            return self._fallback[key]
        # Nothing found anywhere - raise the normal KeyError so bugs
        # (typos in translation keys) are still visible during development.
        raise KeyError(key)

    def get(self, key, default=None):
        try:
            return self[key]
        except KeyError:
            return default


class Language:
    """
    Language class for managing multilingual support using JSON language files.
    """

    def __init__(self):
        """Initialize the language system and load all translation files."""
        self.lang_codes = lang_codes
        # Directory containing translation files
        self.lang_dir = Path("UltraMusic/locales")
        self.languages = self.load_files()  # Load all language files into memory

    def load_files(self):
        """Load all language JSON files from the locales directory."""
        languages = {}
        for lang_code in self.lang_codes.keys():
            lang_file = self.lang_dir / \
                f"{lang_code}.json"  # Path to language file
            if lang_file.exists():
                with open(lang_file, "r", encoding="utf-8") as file:
                    languages[lang_code] = json.load(
                        file)  # Load translations into dict
            else:
                logger.warning(f"⚠️ Language file not found: {lang_file}")
        logger.info(f"🌐 Loaded languages: {', '.join(languages.keys())}")
        return languages

    def get_dict(self, lang_code: str) -> dict:
        """
        Build the translation dict for `lang_code` with automatic
        fallback to English for any key that is missing in that language.
        """
        english = self.languages.get(DEFAULT_LANG, {})

        if lang_code not in self.languages:
            # Unknown / unsupported language code -> just use English.
            return FallbackDict(english, english)

        return FallbackDict(self.languages[lang_code], english)

    async def get_lang(self, chat_id: int) -> dict:
        """
        Get the translation dictionary for a specific chat.

        Reads the chat's saved language preference from MongoDB
        (db.get_lang, cached in memory by MongoDB after the first read).
        Defaults to English if the chat has no preference saved, and
        falls back to English for any individual key missing from the
        selected language file.
        """
        try:
            lang_code = await db.get_lang(chat_id)
        except Exception as e:
            logger.warning(
                f"⚠️ Failed to fetch language for chat {chat_id}, defaulting to English: {e}"
            )
            lang_code = DEFAULT_LANG

        if not lang_code or lang_code not in self.languages:
            lang_code = DEFAULT_LANG

        return self.get_dict(lang_code)

    def language(self):
        def decorator(func):
            @wraps(func)
            async def wrapper(*args, **kwargs):
                fallen = next(
                    (
                        arg
                        for arg in args
                        if hasattr(arg, "chat") or hasattr(arg, "message")
                    ),
                    None,
                )

                if hasattr(fallen, "chat"):
                    chat = fallen.chat
                elif hasattr(fallen, "message"):
                    chat = fallen.message.chat

                if chat.id in db.blacklisted:
                    return await chat.leave()

                # Read this chat's language preference from MongoDB
                # (falls back to English automatically if unset/unknown).
                lang_dict = await self.get_lang(chat.id)

                setattr(fallen, "lang", lang_dict)
                return await func(*args, **kwargs)

            return wrapper

        return decorator
