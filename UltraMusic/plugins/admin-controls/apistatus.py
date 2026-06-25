# ==============================================================================
# apistatus.py - ArtistBots API Key Statistics (Sudo Only)
# ==============================================================================
# Command:
#   /apistatus  — show per-key success/failure stats and consecutive-fail counter
#
# Requires sudo permissions.
# ==============================================================================

from pyrogram import filters, types

from UltraMusic import app, config, lang, yt


@app.on_message(filters.command(["apistatus"]) & app.sudo_filter)
@lang.language()
async def _api_status(_, m: types.Message):
    try:
        await m.delete()
    except Exception:
        pass

    rows = yt.get_api_stats()
    consec = yt._api_consec_fails

    if not rows and not config.API_KEYS:
        return await m.reply_text(
            "<blockquote>ℹ️ <b>لم يتم تهيئة ArtistBots API.</b>\n"
            "أضف <code>API_URL</code> و<code>API_KEYS</code> في .env لتفعيله.</blockquote>"
        )

    if not rows:
        return await m.reply_text(
            "<blockquote>📊 <b>إحصائيات API</b>\n\n"
            "لم يُستخدم أي مفتاح حتى الآن منذ آخر تشغيل.</blockquote>"
        )

    lines = ["<blockquote><b>📊 إحصائيات ArtistBots API</b></blockquote>\n"]
    for i, r in enumerate(rows, 1):
        total = r["ok"] + r["fail"]
        bar_ok   = "█" * (r["pct"] // 10)
        bar_fail = "░" * (10 - r["pct"] // 10)
        lines.append(
            f"<b>#{i}</b> <code>{r['masked']}</code>\n"
            f"  ✅ نجاح: <b>{r['ok']}</b>  ❌ فشل: <b>{r['fail']}</b>  "
            f"({r['pct']}%)\n"
            f"  [{bar_ok}{bar_fail}]\n"
            f"  آخر فشل: {r['last_fail']}"
        )

    # Global counters
    total_ok   = sum(r["ok"]   for r in rows)
    total_fail = sum(r["fail"] for r in rows)
    total_all  = total_ok + total_fail
    overall_pct = round(total_ok / total_all * 100) if total_all else 0

    lines.append(
        f"\n<b>الإجمالي:</b> {total_ok} نجاح / {total_fail} فشل "
        f"({overall_pct}% معدل نجاح)"
    )
    lines.append(
        f"<b>فشل متتالي حالي:</b> <code>{consec}</code> "
        f"(التنبيه عند {getattr(config, 'API_ALERT_THRESHOLD', 5)})"
    )

    await m.reply_text("\n".join(lines))
