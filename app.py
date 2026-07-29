import os
import asyncio
import threading
import logging
import traceback
from flask import Flask, jsonify, request, render_template
from flask_cors import CORS
from telethon import TelegramClient
from telethon.sessions import StringSession
from telethon.tl.types import MessageMediaDocument, DocumentAttributeVideo, DocumentAttributeFilename

logging.basicConfig(level=logging.INFO)

app = Flask(__name__)
CORS(app)

# === ENV o'zgaruvchilar ===
API_ID = os.environ.get("API_ID")
API_HASH = os.environ.get("API_HASH")
SESSION_STRING = os.environ.get("SESSION_STRING")
BOT_USERNAME = os.environ.get("BOT_USERNAME", "")
CHANNEL_USERNAME = os.environ.get("CHANNEL_USERNAME")

if API_ID:
    API_ID = int(API_ID)

# === Event Loop va Telethon sozlash (Xavfsiz usul) ===
tele_loop = asyncio.new_event_loop()
asyncio.set_event_loop(tele_loop)

tele_client = None
if API_ID and API_HASH and SESSION_STRING:
    tele_client = TelegramClient(
        StringSession(SESSION_STRING),
        API_ID,
        API_HASH,
        loop=tele_loop
    )

async def startup():
    if tele_client and not tele_client.is_connected():
        await tele_client.connect()
        logging.info("Telethon muvaffaqiyatli ulandi!")

def run_tele_loop():
    asyncio.set_event_loop(tele_loop)
    tele_loop.run_until_complete(startup())
    tele_loop.run_forever()

# Telethon loop'ini fonda yuritish
if tele_client:
    tele_thread = threading.Thread(target=run_tele_loop, daemon=True)
    tele_thread.start()

def run_async(coro):
    future = asyncio.run_coroutine_threadsafe(coro, tele_loop)
    return future.result(timeout=60)

async def ensure_connected():
    if not tele_client:
        raise Exception("Telegram API konfiguratsiyasi yo'q (ENV o'zgaruvchilarni tekshiring)!")
    if not tele_client.is_connected():
        await tele_client.connect()
    if not await tele_client.is_user_authorized():
        raise Exception("SESSION_STRING yaroqsiz yoki eskirgan!")

# === Kanal postlarini olish ===
async def fetch_channel_posts(limit=50):
    await ensure_connected()
    posts = []
    
    target_channel = CHANNEL_USERNAME.strip() if CHANNEL_USERNAME else ""
    if not target_channel:
        raise Exception("CHANNEL_USERNAME ko'rsatilmagan!")

    async for msg in tele_client.iter_messages(target_channel, limit=limit):
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

        channel_clean = target_channel.lstrip("@")
        link = f"https://t.me/{channel_clean}/{msg.id}"

        title = msg.text or file_name or f"Video #{msg.id}"
        if len(title) > 80:
            title = title[:80] + "..."
        title = title.split("\n")[0].strip() or f"Video #{msg.id}"

        date_str = msg.date.strftime("%d.%m.%Y %H:%M") if msg.date else ""

        posts.append({
            "id": msg.id,
            "title": title,
            "link": link,
            "size_mb": round(size_mb, 1),
            "date": date_str,
            "views": msg.views or 0,
        })

    return posts

# === Botga xabar yuborish ===
async def send_to_bot(link):
    await ensure_connected()
    await tele_client.send_message(BOT_USERNAME, link)
    return True

# === Flask routes ===

@app.route("/")
def index():
    channel = CHANNEL_USERNAME or "@kanal"
    return render_template("index.html", channel=channel)

@app.route("/api/posts")
def api_posts():
    limit = int(request.args.get("limit", 50))
    try:
        posts = run_async(fetch_channel_posts(limit))
        return jsonify({"ok": True, "posts": posts, "total": len(posts)})
    except Exception as e:
        logging.error(f"Posts xatosi: {traceback.format_exc()}")
        return jsonify({"ok": False, "error": str(e)}), 500

@app.route("/api/send", methods=["POST"])
def api_send():
    data = request.json or {}
    link = data.get("link", "").strip()
    if not link:
        return jsonify({"ok": False, "error": "Havola yo'q"}), 400
    try:
        run_async(send_to_bot(link))
        return jsonify({"ok": True, "message": "Bot yuklashni boshladi!"})
    except Exception as e:
        logging.error(f"Send xatosi: {traceback.format_exc()}")
        return jsonify({"ok": False, "error": str(e)}), 500

@app.route("/health")
def health():
    return jsonify({"status": "ok"})

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
