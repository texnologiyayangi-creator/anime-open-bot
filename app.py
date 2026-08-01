import os
import re
import asyncio
import threading
import logging
from flask import Flask, jsonify, request, render_template
from flask_cors import CORS
from telethon import TelegramClient
from telethon.sessions import StringSession
from telethon.tl.types import (
    MessageMediaDocument, MessageMediaPhoto,
    DocumentAttributeVideo, DocumentAttributeFilename,
    MessageEntityTextUrl, MessageEntityUrl, MessageEntityBold,
    MessageEntityItalic, MessageEntityCode
)

logging.basicConfig(level=logging.INFO)

app = Flask(__name__)
CORS(app)

API_ID = int(os.environ.get("API_ID"))
API_HASH = os.environ.get("API_HASH")
SESSION_STRING = os.environ.get("SESSION_STRING")
BOT_USERNAME = os.environ.get("BOT_USERNAME", "")
CHANNEL_USERNAME = os.environ.get("CHANNEL_USERNAME")

tele_loop = None
tele_client = None
_ready = threading.Event()

def run_tele_loop():
    global tele_loop, tele_client
    tele_loop = asyncio.new_event_loop()
    asyncio.set_event_loop(tele_loop)
    tele_client = TelegramClient(
        StringSession(SESSION_STRING), API_ID, API_HASH, loop=tele_loop
    )
    async def connect():
        await tele_client.connect()
        logging.info("Telethon ulandi!")
        _ready.set()
    tele_loop.run_until_complete(connect())
    tele_loop.run_forever()

tele_thread = threading.Thread(target=run_tele_loop, daemon=True)
tele_thread.start()
_ready.wait(timeout=30)

def run_async(coro):
    future = asyncio.run_coroutine_threadsafe(coro, tele_loop)
    return future.result(timeout=300)

async def ensure_connected():
    if not tele_client.is_connected():
        await tele_client.connect()

def clean_title(text, fallback):
    if not text:
        return fallback
    cleaned = re.sub(r'[*_`\[\]()~>#+=|{}.!]', '', text)
    cleaned = re.sub(r'\s+', ' ', cleaned).strip()
    cleaned = cleaned.split("\n")[0].strip()
    return cleaned if cleaned else fallback

def parse_entities(text, entities):
    if not text:
        return ""

    chars = list(text)
    opens = {}
    closes = {}
    for i in range(len(chars) + 1):
        opens[i] = []
        closes[i] = []

    for ent in (entities or []):
        s = ent.offset
        e = min(ent.offset + ent.length, len(chars))

        if isinstance(ent, MessageEntityTextUrl):
            url = ent.url
            opens[s].append(f'<a href="{url}" data-url="{url}" class="tg-link" onclick="openTgLink(event,this)">')
            closes[e].append('</a>')
        elif isinstance(ent, MessageEntityUrl):
            url = text[s:e]
            opens[s].append(f'<a href="{url}" data-url="{url}" class="tg-link" onclick="openTgLink(event,this)">')
            closes[e].append('</a>')
        elif isinstance(ent, MessageEntityBold):
            opens[s].append('<b>')
            closes[e].append('</b>')
        elif isinstance(ent, MessageEntityItalic):
            opens[s].append('<i>')
            closes[e].append('</i>')
        elif isinstance(ent, MessageEntityCode):
            opens[s].append('<code>')
            closes[e].append('</code>')

    result = []
    for i, ch in enumerate(chars):
        result.extend(opens.get(i, []))
        if ch == '&':
            result.append('&amp;')
        elif ch == '<':
            result.append('&lt;')
        elif ch == '>':
            result.append('&gt;')
        elif ch == '\n':
            result.append('<br>')
        else:
            result.append(ch)
        result.extend(closes.get(i + 1, []))

    result.extend(closes.get(len(chars), []))
    html = ''.join(result)

    # Markdown [nom](url) formatini ham aniqlash
    def replace_md_link(m):
        name = m.group(1)
        url = m.group(2)
        return f'<a href="{url}" data-url="{url}" class="tg-link" onclick="openTgLink(event,this)">{name}</a>'

    html = re.sub(r'\[([^\]<>]+)\]\((https?://[^\)]+)\)', replace_md_link, html)
    return html

async def fetch_channel_posts(offset_id=0, limit=50):
    await ensure_connected()
    posts = []
    channel = CHANNEL_USERNAME.lstrip("@")
    count = 0
    async for msg in tele_client.iter_messages(CHANNEL_USERNAME, limit=None, offset_id=offset_id, reverse=False):
        if msg.media is None:
            continue
        is_video = False
        file_name = None
        size_mb = 0
        if isinstance(msg.media, MessageMediaDocument):
            doc = msg.media.document
            size_mb = doc.size / 1024 / 1024
            for attr in doc.attributes:
                if isinstance(attr, DocumentAttributeVideo):
                    is_video = True
                if isinstance(attr, DocumentAttributeFilename):
                    file_name = attr.file_name
            if doc.mime_type and doc.mime_type.startswith("video"):
                is_video = True
        if not is_video:
            continue
        link = f"https://t.me/{channel}/{msg.id}"
        fallback = file_name or f"Video #{msg.id}"
        title = clean_title(msg.text, fallback)
        if len(title) > 80:
            title = title[:80].strip()
        date_str = msg.date.strftime("%d.%m.%Y %H:%M") if msg.date else ""
        posts.append({
            "id": msg.id, "title": title, "link": link,
            "size_mb": round(size_mb, 1), "date": date_str, "views": msg.views or 0,
        })
        count += 1
        if count >= limit:
            break
    return posts

async def fetch_all_messages(offset_id=0, limit=50):
    await ensure_connected()
    messages = []
    channel = CHANNEL_USERNAME.lstrip("@")
    count = 0
    async for msg in tele_client.iter_messages(CHANNEL_USERNAME, limit=None, offset_id=offset_id, reverse=False):
        msg_type = "text"
        size_mb = 0
        file_name = None

        if isinstance(msg.media, MessageMediaPhoto):
            msg_type = "photo"
        elif isinstance(msg.media, MessageMediaDocument):
            doc = msg.media.document
            size_mb = doc.size / 1024 / 1024
            msg_type = "document"
            for attr in doc.attributes:
                if isinstance(attr, DocumentAttributeVideo):
                    msg_type = "video"
                if isinstance(attr, DocumentAttributeFilename):
                    file_name = attr.file_name
            if doc.mime_type:
                if doc.mime_type.startswith("video"):
                    msg_type = "video"
                elif doc.mime_type.startswith("audio"):
                    msg_type = "audio"
        elif msg.media:
            msg_type = "media"

        link = f"https://t.me/{channel}/{msg.id}"
        raw_text = msg.text or file_name or ""
        html_text = parse_entities(raw_text, msg.entities or [])
        # plain text - qidiruv uchun
        plain_text = re.sub(r'[*_`]', '', raw_text).strip() if raw_text else ""
        date_str = msg.date.strftime("%d.%m.%Y %H:%M") if msg.date else ""

        msg_link = f"https://t.me/{channel}/{msg.id}"
        messages.append({
            "id": msg.id, "type": msg_type,
            "text": html_text,
            "plain": plain_text,
            "link": msg_link,
            "size_mb": round(size_mb, 1) if size_mb else 0,
            "date": date_str, "views": msg.views or 0,
        })
        count += 1
        if count >= limit:
            break
    return messages

async def send_to_bot(link, name=""):
    await ensure_connected()
    # Avval URL yuboramiz — bot uni qabul qilib nom so'raydi
    await tele_client.send_message(BOT_USERNAME, link)
    if name:
        # Bot nom so'rashga vaqt berish uchun 3 sekund kutamiz
        await asyncio.sleep(3)
        await tele_client.send_message(BOT_USERNAME, name)
    return True

@app.route("/")
def index():
    return render_template("index.html", channel=CHANNEL_USERNAME or "@kanal")

@app.route("/api/posts")
def api_posts():
    limit = int(request.args.get("limit", 50))
    try:
        offset_id = int(request.args.get("offset_id", 0) or 0)
        posts = run_async(fetch_channel_posts(offset_id=offset_id, limit=limit))
        has_more = len(posts) >= limit
        next_offset = int(posts[-1]["id"]) if posts else 0
        return jsonify({"ok": True, "posts": posts, "has_more": has_more, "next_offset": next_offset})
    except Exception as e:
        logging.error(f"Posts xatosi: {e}")
        return jsonify({"ok": False, "error": str(e)}), 500

@app.route("/api/messages")
def api_messages():
    limit = int(request.args.get("limit", 50))
    try:
        offset_id = int(request.args.get("offset_id", 0) or 0)
        messages = run_async(fetch_all_messages(offset_id=offset_id, limit=limit))
        has_more = len(messages) >= limit
        next_offset = int(messages[-1]["id"]) if messages else 0
        return jsonify({"ok": True, "messages": messages, "has_more": has_more, "next_offset": next_offset})
    except Exception as e:
        logging.error(f"Messages xatosi: {e}")
        return jsonify({"ok": False, "error": str(e)}), 500

@app.route("/api/send", methods=["POST"])
def api_send():
    data = request.json or {}
    link = (data.get("link") or "").strip()
    name = (data.get("name") or "").strip()
    if not link:
        logging.error(f"api_send: link yo'q, data={data}")
        return jsonify({"ok": False, "error": "Havola yo'q"}), 400
    try:
        run_async(send_to_bot(link, name))
        return jsonify({"ok": True, "message": "Bot yuklashni boshladi!"})
    except Exception as e:
        logging.error(f"Send xatosi: {e}")
        return jsonify({"ok": False, "error": str(e)}), 500

@app.route("/api/send_bulk", methods=["POST"])
def api_send_bulk():
    data = request.json or {}
    links = data.get("links") or []
    prefix = (data.get("prefix") or "").strip()
    if not links:
        return jsonify({"ok": False, "error": "Havolalar yo'q"}), 400
    results = []
    for i, link in enumerate(links):
        try:
            name = f"{prefix} {i+1}" if prefix else ""
            run_async(send_to_bot(link, name))
            results.append({"link": link, "ok": True})
        except Exception as e:
            results.append({"link": link, "ok": False, "error": str(e)})
    return jsonify({"ok": True, "results": results})

@app.route("/health")
def health():
    return jsonify({"status": "ok"})

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
