"""
Radar - new launch alert bot for Telegram.

Sources:
  - DexScreener  : newest token profiles (Solana / Base / BNB)
  - GeckoTerminal: newest pools (CoinGecko's DEX data, free, no key)
  - CoinMarketCap: newest listings (optional, needs free API key)

Setup:
  pip install requests
  export BOT_TOKEN="your token from BotFather"
  export CMC_API_KEY="optional CoinMarketCap key"
  python bot.py

Then open your bot in Telegram and send /start.
Commands: /start (subscribe), /stop (unsubscribe), /status
"""
import os
import json
import time
import html
import threading
import requests
from urllib.parse import urlparse

BOT_TOKEN = os.environ["BOT_TOKEN"]
CMC_KEY = os.environ.get("CMC_API_KEY", "").strip()
POLL_SECONDS = int(os.environ.get("POLL_SECONDS", "90"))
MIN_LIQ_USD = float(os.environ.get("MIN_LIQ_USD", "5000"))
# 1 = only alert on tokens that have a website, an X account AND a Telegram link
REQUIRE_SOCIALS = os.environ.get("REQUIRE_SOCIALS", "1") == "1"

# chain names differ per source: (dexscreener id, geckoterminal id)
CHAINS = {"solana": "solana", "base": "base", "bsc": "bsc"}

API = f"https://api.telegram.org/bot{BOT_TOKEN}"
SUBS_FILE = "subscribers.json"
SEEN_FILE = "seen.json"
lock = threading.Lock()


def load(path, default):
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return default


def save(path, data):
    with lock:
        with open(path, "w") as f:
            json.dump(data, f)


def tg(method, **params):
    try:
        r = requests.post(f"{API}/{method}", json=params, timeout=40)
        return r.json()
    except Exception as e:
        print("telegram error:", e)
        return {}


def broadcast(text):
    for chat_id in load(SUBS_FILE, []):
        tg("sendMessage", chat_id=chat_id, text=text, parse_mode="HTML",
           disable_web_page_preview=True)
        time.sleep(0.05)


def esc(x):
    return html.escape(str(x if x is not None else "?"))


def money(v):
    try:
        v = float(v)
    except Exception:
        return "?"
    if v >= 1_000_000:
        return f"${v/1_000_000:.2f}M"
    if v >= 1_000:
        return f"${v/1_000:.1f}K"
    return f"${v:.0f}"


def vet_links(chain, token):
    links = [f'<a href="https://dexscreener.com/{chain}/{token}">DexScreener</a>']
    if chain == "solana":
        links.append(f'<a href="https://rugcheck.xyz/tokens/{token}">RugCheck</a>')
        links.append(f'<a href="https://app.bubblemaps.io/sol/token/{token}">Bubblemaps</a>')
    return " | ".join(links)


def host(url):
    h = urlparse(url).netloc.lower()
    return h[4:] if h.startswith("www.") else h


NOT_A_SITE = ("discord.gg", "discord.com", "tiktok.com", "instagram.com",
              "youtube.com", "github.com", "medium.com", "reddit.com")


def classify(links):
    """links: list of dicts with type/label/url -> {'site','x','tg'} urls found."""
    found = {}
    for l in links or []:
        url = (l.get("url") or "").strip()
        if not url.startswith("http"):
            continue
        kind = (l.get("type") or l.get("label") or "").lower()
        h = host(url)
        if h in ("t.me", "telegram.me") or kind == "telegram":
            found.setdefault("tg", url)
        elif h in ("x.com", "twitter.com") or kind in ("twitter", "x"):
            found.setdefault("x", url)
        elif kind in ("website", "web", "site") or h not in NOT_A_SITE:
            found.setdefault("site", url)
    return found


def complete(found):
    return all(k in found for k in ("site", "x", "tg"))


def socials_line(found):
    parts = []
    if "site" in found:
        parts.append(f'<a href="{esc(found["site"])}">Website</a>')
    if "x" in found:
        parts.append(f'<a href="{esc(found["x"])}">X</a>')
    if "tg" in found:
        parts.append(f'<a href="{esc(found["tg"])}">Telegram</a>')
    return ("Socials: " + " | ".join(parts) + "\n") if parts else ""


def dex_socials(chain, token):
    """Look up a token's socials on DexScreener (used for GeckoTerminal pools)."""
    try:
        pairs = requests.get(
            f"https://api.dexscreener.com/tokens/v1/{chain}/{token}", timeout=20).json()
    except Exception:
        return {}
    for p in pairs if isinstance(pairs, list) else []:
        info = p.get("info") or {}
        found = classify((info.get("websites") or []) + (info.get("socials") or []))
        if found:
            return found
    return {}


# ---------- sources: each returns list of (unique_key, message) ----------

def src_dexscreener():
    out = []
    r = requests.get("https://api.dexscreener.com/token-profiles/latest/v1", timeout=30)
    for it in r.json():
        chain = it.get("chainId")
        addr = it.get("tokenAddress")
        if chain not in CHAINS or not addr:
            continue
        desc = (it.get("description") or "").strip()[:160]
        found = classify(it.get("links"))
        if REQUIRE_SOCIALS and not complete(found):
            continue
        msg = (
            f"🆕 <b>New profile on DexScreener</b> ({esc(chain)})\n"
            f"CA: <code>{esc(addr)}</code>\n"
            + (f"{esc(desc)}\n" if desc else "")
            + socials_line(found)
            + vet_links(chain, addr)
        )
        out.append((f"dex:{chain}:{addr}", msg))
    return out


def src_geckoterminal():
    out = []
    for chain, net in CHAINS.items():
        r = requests.get(
            f"https://api.geckoterminal.com/api/v2/networks/{net}/new_pools",
            headers={"Accept": "application/json"}, timeout=30)
        for p in r.json().get("data", []):
            a = p.get("attributes", {})
            liq = float(a.get("reserve_in_usd") or 0)
            if liq < MIN_LIQ_USD:
                continue
            base = (p.get("relationships", {}).get("base_token", {})
                    .get("data", {}).get("id", ""))
            token = base.split("_", 1)[-1] if base else ""
            if not token:
                continue
            found = dex_socials(chain, token)
            time.sleep(0.3)
            if REQUIRE_SOCIALS and not complete(found):
                continue  # not alerted or recorded, so it is re-checked next cycle
            msg = (
                f"🚀 <b>New pool: {esc(a.get('name'))}</b> ({esc(chain)})\n"
                f"Liquidity: {money(liq)} | FDV: {money(a.get('fdv_usd'))}\n"
                f"CA: <code>{esc(token)}</code>\n"
                + socials_line(found)
                + vet_links(chain, token)
            )
            out.append((f"gt:{p.get('id')}", msg))
        time.sleep(2)  # stay inside free rate limits
    return out


def src_cmc():
    if not CMC_KEY:
        return []
    out = []
    r = requests.get(
        "https://pro-api.coinmarketcap.com/v1/cryptocurrency/listings/latest",
        headers={"X-CMC_PRO_API_KEY": CMC_KEY},
        params={"limit": 20, "sort": "date_added", "sort_dir": "desc"}, timeout=30)
    coins = r.json().get("data", [])
    infos = {}
    if coins:
        ir = requests.get(
            "https://pro-api.coinmarketcap.com/v2/cryptocurrency/info",
            headers={"X-CMC_PRO_API_KEY": CMC_KEY},
            params={"id": ",".join(str(c["id"]) for c in coins)}, timeout=30)
        infos = ir.json().get("data", {}) or {}
    for c in coins:
        urls = (infos.get(str(c["id"])) or {}).get("urls", {}) or {}
        links = [{"type": "website", "url": u} for u in urls.get("website", [])]
        links += [{"type": "twitter", "url": u} for u in urls.get("twitter", [])]
        links += [{"url": u} for u in urls.get("chat", []) if "t.me/" in u]
        found = classify(links)
        if REQUIRE_SOCIALS and not complete(found):
            continue
        usd = c.get("quote", {}).get("USD", {})
        msg = (
            f"📋 <b>New on CoinMarketCap: {esc(c.get('name'))} (${esc(c.get('symbol'))})</b>\n"
            f"Price: {esc(usd.get('price'))} | MCap: {money(usd.get('market_cap'))}\n"
            + socials_line(found)
            + f'<a href="https://coinmarketcap.com/currencies/{esc(c.get("slug"))}/">View on CMC</a>'
        )
        out.append((f"cmc:{c.get('id')}", msg))
    return out


SOURCES = {"dexscreener": src_dexscreener,
           "geckoterminal": src_geckoterminal,
           "coinmarketcap": src_cmc}


def scanner():
    while True:
        seen = load(SEEN_FILE, {})
        for name, fn in SOURCES.items():
            try:
                items = fn()
            except Exception as e:
                print(f"{name} failed:", e)
                continue
            first_run = name not in seen
            known = set(seen.get(name, []))
            for key, msg in items:
                if key in known:
                    continue
                known.add(key)
                if not first_run:  # first run only records, avoids flooding
                    broadcast(msg)
            seen[name] = list(known)[-1500:]
        save(SEEN_FILE, seen)
        time.sleep(POLL_SECONDS)


def commands():
    offset = 0
    while True:
        res = tg("getUpdates", offset=offset, timeout=30)
        for u in res.get("result", []):
            offset = u["update_id"] + 1
            m = u.get("message") or {}
            chat_id = (m.get("chat") or {}).get("id")
            text = (m.get("text") or "").strip().lower()
            if not chat_id:
                continue
            subs = load(SUBS_FILE, [])
            if text.startswith("/start"):
                if chat_id not in subs:
                    subs.append(chat_id)
                    save(SUBS_FILE, subs)
                tg("sendMessage", chat_id=chat_id,
                   text="Radar is on. You'll get new launch alerts from DexScreener, "
                        "GeckoTerminal and CoinMarketCap (Solana, Base, BNB).\n\n"
                        "/stop to pause, /status to check.\n"
                        "Alerts are not endorsements. Always vet the CA yourself.")
            elif text.startswith("/stop"):
                if chat_id in subs:
                    subs.remove(chat_id)
                    save(SUBS_FILE, subs)
                tg("sendMessage", chat_id=chat_id, text="Radar paused. /start to resume.")
            elif text.startswith("/status"):
                tg("sendMessage", chat_id=chat_id,
                   text=f"Subscribers: {len(subs)}\nChains: {', '.join(CHAINS)}\n"
                        f"Min liquidity (GeckoTerminal): ${MIN_LIQ_USD:,.0f}\n"
                        f"CMC: {'on' if CMC_KEY else 'off (no key)'}\n"
                        f"Check every {POLL_SECONDS}s")
            else:
                tg("sendMessage", chat_id=chat_id, text="Commands: /start /stop /status")


if __name__ == "__main__":
    print("Radar running...")
    threading.Thread(target=scanner, daemon=True).start()
    commands()
