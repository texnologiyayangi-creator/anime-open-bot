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
    """Telegram entities dan havola va formatlashni HTML ga aylantiradi"""
    if not text or not entities:
        return escape_html(text or "")

    # Har bir belgi uchun teglar
    chars = list(text)
    opens = {i: [] for i in range(len(chars))}
    closes = {i: [] for i in range(len(chars))}

    for ent in entities:
        s = ent.offset
        e = ent.offset + ent.length

        if isinstance(ent, MessageEntityTextUrl):
            opens[s].append(f'<a href="{ent.url}" target="_blank" class="tg-link">')
            closes[e-1].append('</a>')
        elif isinstance(ent, MessageEntityUrl):
            url = text[s:e]
            opens[s].append(f'<a href="{url}" target="_blank" class="tg-link">')
            closes[e-1].append('</a>')
        elif isinstance(ent, MessageEntityBold):
            opens[s].append('<b>')
            closes[e-1].append('</b>')
        elif isinstance(ent, MessageEntityItalic):
            opens[s].append('<i>')
            closes[e-1].append('</i>')
        elif isinstance(ent, MessageEntityCode):
            opens[s].append('<code>')
            closes[e-1].append('</code>')

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
        # closes teskari tartibda
        for tag in reversed(closes.get(i, [])):
            result.append(tag)

    return ''.join(result)

def escape_html(text):
    return text.replace('&','&amp;').replace('<','&lt;').replace('>','&gt;').replace('\n','<br>')

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

        # Entities bilan HTML formatga o'tkazish
        html_text = parse_entities(raw_text, msg.entities or [])

        date_str = msg.date.strftime("%d.%m.%Y %H:%M") if msg.date else ""

        messages.append({
            "id": msg.id, "type": msg_type,
            "text": html_text,  # HTML formatda
            "link": link, "size_mb": round(size_mb, 1) if size_mb else 0,
            "date": date_str, "views": msg.views or 0,
        })
        count += 1
        if count >= limit:
            break
    return messages

async def send_to_bot(link, name=""):
    await ensure_connected()
    await tele_client.send_message(BOT_USERNAME, link)
    if name:
        await asyncio.sleep(0.5)
        await tele_client.send_message(BOT_USERNAME, name)
    return True

@app.route("/")
def index():
    return render_template("index.html", channel=CHANNEL_USERNAME or "@kanal")

@app.route("/api/posts")
def api_posts():
    limit = int(request.args.get("limit", 50))
    offset_id = int(request.args.get("offset_id", 0))
    try:
        posts = run_async(fetch_channel_posts(offset_id=offset_id, limit=limit))
        has_more = len(posts) >= limit
        next_offset = posts[-1]["id"] if posts else 0
        return jsonify({"ok": True, "posts": posts, "has_more": has_more, "next_offset": next_offset})
    except Exception as e:
        logging.error(f"Posts xatosi: {e}")
        return jsonify({"ok": False, "error": str(e)}), 500

@app.route("/api/messages")
def api_messages():
    limit = int(request.args.get("limit", 50))
    offset_id = int(request.args.get("offset_id", 0))
    try:
        messages = run_async(fetch_all_messages(offset_id=offset_id, limit=limit))
        has_more = len(messages) >= limit
        next_offset = messages[-1]["id"] if messages else 0
        return jsonify({"ok": True, "messages": messages, "has_more": has_more, "next_offset": next_offset})
    except Exception as e:
        logging.error(f"Messages xatosi: {e}")
        return jsonify({"ok": False, "error": str(e)}), 500

@app.route("/api/send", methods=["POST"])
def api_send():
    data = request.json or {}
    link = data.get("link", "").strip()
    name = data.get("name", "").strip()
    if not link:
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
    links = data.get("links", [])
    prefix = data.get("prefix", "").strip()
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
