"""
Radar - one-shot run for GitHub Actions (free hosting).

Each run: 1) picks up /start and /stop messages, 2) scans for new launches,
3) sends alerts to subscribers, 4) saves state (the workflow caches it).

Needs bot.py in the same folder. Secrets: BOT_TOKEN (required),
CHAT_ID (recommended, comma-separated), CMC_API_KEY (optional).
"""
import os
import bot


def permanent_subscribers():
    out = []
    for part in os.environ.get("CHAT_ID", "").split(","):
        part = part.strip()
        if part.lstrip("-").isdigit():
            out.append(int(part))
    return out


def handle_commands():
    subs = bot.load(bot.SUBS_FILE, [])
    for cid in permanent_subscribers():
        if cid not in subs:
            subs.append(cid)
    offset = bot.load("offset.json", 0)
    res = bot.tg("getUpdates", offset=offset, timeout=0)
    for u in res.get("result", []):
        offset = u["update_id"] + 1
        m = u.get("message") or {}
        chat_id = (m.get("chat") or {}).get("id")
        text = (m.get("text") or "").strip().lower()
        if not chat_id:
            continue
        if text.startswith("/start"):
            if chat_id not in subs:
                subs.append(chat_id)
            reply = ("Radar is on. You'll get new launch alerts (Solana, Base, BNB) "
                     "that have a website, X and Telegram. Alerts can arrive a few "
                     "minutes after a launch.\n\n/stop to pause.\n"
                     "Alerts are not endorsements. Always vet the CA yourself.")
        elif text.startswith("/stop"):
            if chat_id in subs:
                subs.remove(chat_id)
            reply = "Radar paused. Send /start to resume."
        else:
            reply = "Commands: /start /stop"
        bot.tg("sendMessage", chat_id=chat_id, text=reply)
    bot.save(bot.SUBS_FILE, subs)
    bot.save("offset.json", offset)


def scan_once():
    seen = bot.load(bot.SEEN_FILE, {})
    for name, fn in bot.SOURCES.items():
        try:
            items = fn()
        except Exception as e:
            print(f"{name} failed:", e)
            continue
        first_run = name not in seen
        known = list(seen.get(name, []))
        known_set = set(known)
        for key, msg in items:
            if key in known_set:
                continue
            known.append(key)
            known_set.add(key)
            if not first_run:  # first run only records, avoids flooding
                bot.broadcast(msg)
        seen[name] = known[-3000:]
    bot.save(bot.SEEN_FILE, seen)


if __name__ == "__main__":
    handle_commands()
    scan_once()
    print("done")
