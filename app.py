import os
import asyncio
import threading
import logging
from flask import Flask, jsonify, request, render_template
from flask_cors import CORS
from telethon import TelegramClient
from telethon.sessions import StringSession
from telethon.tl.types import MessageMediaDocument, MessageMediaPhoto, DocumentAttributeVideo, DocumentAttributeFilename

logging.basicConfig(level=logging.INFO)

app = Flask(__name__)
CORS(app)

API_ID = int(os.environ.get("API_ID"))
API_HASH = os.environ.get("API_HASH")
SESSION_STRING = os.environ.get("SESSION_STRING")
BOT_USERNAME = os.environ.get("BOT_USERNAME", "")
CHANNEL_USERNAME = os.environ.get("CHANNEL_USERNAME")

# === Event loop va client thread ichida yaratiladi ===
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
    return future.result(timeout=60)

async def ensure_connected():
    if not tele_client.is_connected():
        await tele_client.connect()

# === Faqat video postlarni olish ===
async def fetch_channel_posts(limit=100):
    await ensure_connected()
    posts = []
    channel = CHANNEL_USERNAME.lstrip("@")
    async for msg in tele_client.iter_messages(CHANNEL_USERNAME, limit=limit):
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
        title = msg.text or file_name or f"Video #{msg.id}"
        if len(title) > 80:
            title = title[:80] + "..."
        title = title.split("\n")[0].strip() or f"Video #{msg.id}"
        date_str = msg.date.strftime("%d.%m.%Y %H:%M") if msg.date else ""
        posts.append({
            "id": msg.id, "title": title, "link": link,
            "size_mb": round(size_mb, 1), "date": date_str, "views": msg.views or 0,
        })
    return posts

# === Barcha xabarlarni olish (Telegram kabi) ===
async def fetch_all_messages(limit=100):
    await ensure_connected()
    messages = []
    channel = CHANNEL_USERNAME.lstrip("@")
    async for msg in tele_client.iter_messages(CHANNEL_USERNAME, limit=limit):
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
        text = msg.text or file_name or ""
        date_str = msg.date.strftime("%d.%m.%Y %H:%M") if msg.date else ""

        messages.append({
            "id": msg.id, "type": msg_type, "text": text,
            "link": link, "size_mb": round(size_mb, 1) if size_mb else 0,
            "date": date_str, "views": msg.views or 0,
        })
    return messages

# === Botga xabar yuborish (nom bilan) ===
async def send_to_bot(link, name=""):
    await ensure_connected()
    # Avval havolani yuboramiz
    await tele_client.send_message(BOT_USERNAME, link)
    # Agar nom berilgan bo'lsa — keyin nom yuboramiz
    if name:
        import asyncio as _asyncio
        await _asyncio.sleep(0.5)
        await tele_client.send_message(BOT_USERNAME, name)
    return True

# === Flask routes ===
@app.route("/")
def index():
    return render_template("index.html", channel=CHANNEL_USERNAME or "@kanal")

@app.route("/api/posts")
def api_posts():
    limit = int(request.args.get("limit", 100))
    try:
        posts = run_async(fetch_channel_posts(limit))
        return jsonify({"ok": True, "posts": posts, "total": len(posts)})
    except Exception as e:
        logging.error(f"Posts xatosi: {e}")
        return jsonify({"ok": False, "error": str(e)}), 500

@app.route("/api/messages")
def api_messages():
    limit = int(request.args.get("limit", 100))
    try:
        messages = run_async(fetch_all_messages(limit))
        return jsonify({"ok": True, "messages": messages, "total": len(messages)})
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
