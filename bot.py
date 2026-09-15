# ==========================================================
#   SERIUS BOT — bitta faylda (bot.py)
#   Yaratuvchi: Behruzbek Salmonjonov
# ==========================================================
#
# O'RNATISH (Pydroid3 terminalida):
#   pip install python-telegram-bot requests pypdf
#
# MUHIM: google-genai kutubxonasi Pydroid3'da (Rust talab qilgani
# uchun) o'rnatilmaydi, shuning uchun bu yerda oddiy "requests"
# orqali to'g'ridan-to'g'ri HTTP so'rov ishlatiladi.
#
# ISHGA TUSHIRISH:
#   1. Pastdagi "1) KONFIGURATSIYA" bo'limiga BOT_TOKEN, GEMINI_API_KEY
#      va ADMIN_IDS_RAW qiymatlarini yozing
#   2. Shu faylni ishga tushiring (▶️ Run)
#
# ⚠️ ESLATMA: Bu faylda API kalitlar ochiq matn sifatida saqlanadi.
# Bu faylni hech qachon boshqa birov bilan ulashmang yoki GitHub'ga
# ochiq (public) repozitoriyga yuklamang — bu parolingizni ochiq
# qo'yish bilan barobar.
# ==========================================================


import os
import json
import sqlite3
import datetime
import threading
import logging
import base64
import urllib.parse
import io

import requests

from telegram import (
    Update, ReplyKeyboardMarkup, InlineKeyboardMarkup, InlineKeyboardButton
)
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    CallbackQueryHandler, ContextTypes, filters
)

# ==========================================================
# 1) KONFIGURATSIYA
# ==========================================================
# LOKAL (Pydroid3) ISHLATISH: shu 3 qatorga to'g'ridan-to'g'ri
# qiymat yozing.
#
# RAILWAY'DA ISHLATISH: bu qatorlarni "BU_YERGA..." holida QOLDIRING
# (haqiqiy kalitlarni bu yerga yozmang!). Buning o'rniga Railway
# saytida loyihangiz -> "Variables" bo'limiga o'ting va shu nomlar
# bilan qo'shing: BOT_TOKEN, GEMINI_API_KEY, ADMIN_IDS
# Railway ishga tushganda ularni avtomatik shu yerga joylaydi.
# ==========================================================
BOT_TOKEN = os.getenv("BOT_TOKEN", "BU_YERGA_TELEGRAM_TOKENINGIZNI_YOZING")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "BU_YERGA_GEMINI_KALITINGIZNI_YOZING")

# Admin(lar) Telegram ID raqami(lari). Bir nechta bo'lsa vergul bilan ajrating.
ADMIN_IDS_RAW = os.getenv("ADMIN_IDS", "BU_YERGA_OZINGIZNING_TELEGRAM_ID_RAQAMINGIZNI_YOZING")

ADMIN_IDS = set()
for part in ADMIN_IDS_RAW.split(","):
    part = part.strip()
    if part.isdigit():
        ADMIN_IDS.add(int(part))

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

if not BOT_TOKEN or "BU_YERGA" in BOT_TOKEN:
    raise SystemExit("❌ BOT_TOKEN to'ldirilmagan! Fayl boshidagi CONFIG qismini to'ldiring.")
if not GEMINI_API_KEY or "BU_YERGA" in GEMINI_API_KEY:
    raise SystemExit("❌ GEMINI_API_KEY to'ldirilmagan! Fayl boshidagi CONFIG qismini to'ldiring.")
if not ADMIN_IDS:
    logger.warning("⚠️ ADMIN_IDS to'ldirilmagan — hech kim admin panelga kira olmaydi!")


def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS


# ==========================================================
# 2) DATABASE — SQLite orqali doimiy xotira
# ==========================================================
DB_PATH = "serius_bot.db"
_lock = threading.Lock()


def get_connection():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with _lock:
        conn = get_connection()
        cur = conn.cursor()
        cur.execute("""
            CREATE TABLE IF NOT EXISTS users (
                telegram_id INTEGER PRIMARY KEY,
                username TEXT,
                first_name TEXT,
                joined_at TEXT,
                is_banned INTEGER DEFAULT 0,
                is_premium INTEGER DEFAULT 0,
                selected_mode TEXT DEFAULT 'chat',
                message_count INTEGER DEFAULT 0,
                image_count INTEGER DEFAULT 0,
                chat_history TEXT DEFAULT '[]'
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS bot_settings (
                key TEXT PRIMARY KEY,
                value TEXT
            )
        """)
        conn.commit()
        conn.close()


def add_or_update_user(telegram_id, username=None, first_name=None):
    with _lock:
        conn = get_connection()
        cur = conn.cursor()
        cur.execute("SELECT telegram_id FROM users WHERE telegram_id = ?", (telegram_id,))
        exists = cur.fetchone()
        if exists:
            cur.execute(
                "UPDATE users SET username = ?, first_name = ? WHERE telegram_id = ?",
                (username, first_name, telegram_id)
            )
        else:
            cur.execute(
                "INSERT INTO users (telegram_id, username, first_name, joined_at) VALUES (?, ?, ?, ?)",
                (telegram_id, username, first_name, datetime.datetime.now().isoformat())
            )
        conn.commit()
        conn.close()


def get_user(telegram_id):
    with _lock:
        conn = get_connection()
        cur = conn.cursor()
        cur.execute("SELECT * FROM users WHERE telegram_id = ?", (telegram_id,))
        row = cur.fetchone()
        conn.close()
        return dict(row) if row else None


def get_all_users():
    with _lock:
        conn = get_connection()
        cur = conn.cursor()
        cur.execute("SELECT * FROM users ORDER BY joined_at DESC")
        rows = cur.fetchall()
        conn.close()
        return [dict(r) for r in rows]


def set_banned(telegram_id, banned: bool):
    with _lock:
        conn = get_connection()
        cur = conn.cursor()
        cur.execute("UPDATE users SET is_banned = ? WHERE telegram_id = ?", (1 if banned else 0, telegram_id))
        conn.commit()
        conn.close()


def set_premium(telegram_id, premium: bool):
    with _lock:
        conn = get_connection()
        cur = conn.cursor()
        cur.execute("UPDATE users SET is_premium = ? WHERE telegram_id = ?", (1 if premium else 0, telegram_id))
        conn.commit()
        conn.close()


def set_mode(telegram_id, mode: str):
    with _lock:
        conn = get_connection()
        cur = conn.cursor()
        cur.execute("UPDATE users SET selected_mode = ? WHERE telegram_id = ?", (mode, telegram_id))
        conn.commit()
        conn.close()


def increment_message_count(telegram_id):
    with _lock:
        conn = get_connection()
        cur = conn.cursor()
        cur.execute("UPDATE users SET message_count = message_count + 1 WHERE telegram_id = ?", (telegram_id,))
        conn.commit()
        conn.close()


def increment_image_count(telegram_id):
    with _lock:
        conn = get_connection()
        cur = conn.cursor()
        cur.execute("UPDATE users SET image_count = image_count + 1 WHERE telegram_id = ?", (telegram_id,))
        conn.commit()
        conn.close()


def get_chat_history(telegram_id):
    user = get_user(telegram_id)
    if not user or not user.get("chat_history"):
        return []
    try:
        return json.loads(user["chat_history"])
    except (json.JSONDecodeError, TypeError):
        return []


def save_chat_history(telegram_id, history_list, max_items=12):
    trimmed = history_list[-max_items:]
    with _lock:
        conn = get_connection()
        cur = conn.cursor()
        cur.execute(
            "UPDATE users SET chat_history = ? WHERE telegram_id = ?",
            (json.dumps(trimmed, ensure_ascii=False), telegram_id)
        )
        conn.commit()
        conn.close()


def clear_chat_history(telegram_id):
    save_chat_history(telegram_id, [])


def clear_all_chat_histories():
    with _lock:
        conn = get_connection()
        cur = conn.cursor()
        cur.execute("UPDATE users SET chat_history = '[]'")
        conn.commit()
        conn.close()


def get_stats():
    with _lock:
        conn = get_connection()
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) as c FROM users")
        total_users = cur.fetchone()["c"]
        cur.execute("SELECT COUNT(*) as c FROM users WHERE message_count > 0")
        active_users = cur.fetchone()["c"]
        cur.execute("SELECT COALESCE(SUM(message_count), 0) as s FROM users")
        total_messages = cur.fetchone()["s"]
        cur.execute("SELECT COALESCE(SUM(image_count), 0) as s FROM users")
        total_images = cur.fetchone()["s"]
        cur.execute("SELECT COUNT(*) as c FROM users WHERE is_premium = 1")
        premium_users = cur.fetchone()["c"]
        cur.execute("SELECT COUNT(*) as c FROM users WHERE is_banned = 1")
        banned_users = cur.fetchone()["c"]
        conn.close()
        return {
            "total_users": total_users,
            "active_users": active_users,
            "total_messages": total_messages,
            "total_images": total_images,
            "premium_users": premium_users,
            "banned_users": banned_users,
        }


def get_setting(key, default=None):
    with _lock:
        conn = get_connection()
        cur = conn.cursor()
        cur.execute("SELECT value FROM bot_settings WHERE key = ?", (key,))
        row = cur.fetchone()
        conn.close()
        return row["value"] if row else default


def set_setting(key, value):
    with _lock:
        conn = get_connection()
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO bot_settings (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, str(value))
        )
        conn.commit()
        conn.close()


# ==========================================================
# 3) GEMINI API — google-genai rasmiy kutubxonasi orqali
#    (yangi "AQ." kalitlar aynan shu kutubxona bilan to'g'ri ishlaydi)
# ==========================================================
# ==========================================================
# 3) GEMINI API — oddiy HTTP so'rov orqali (google-genai kutubxonasi
#    Pydroid3'da Rust talab qilgani uchun ishlatilmaydi)
#
#    Kalit "x-goog-api-key" header orqali yuboriladi (Google'ning
#    rasmiy hujjatlashtirilgan usuli, "AQ." va "AIzaSy" kalitlar
#    uchun ham ishlaydi).
# ==========================================================
TEXT_MODEL = "gemini-flash-lite-latest"


def _gemini_url(model):
    return f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"


def _gemini_headers():
    return {
        "Content-Type": "application/json",
        "x-goog-api-key": GEMINI_API_KEY,
    }


def _call_gemini(payload):
    """Gemini'ga so'rov yuboradi. Xato bo'lsa, ANIQ sababni terminalga chiqaradi."""
    response = requests.post(_gemini_url(TEXT_MODEL), headers=_gemini_headers(), json=payload, timeout=120)

    if response.status_code == 429:
        logger.error(f"Gemini kvota tugadi (429): {response.text}")
        raise QuotaExceededError("Kunlik/daqiqalik so'rov limiti tugadi")

    if response.status_code != 200:
        # MUHIM: Google'dan qaytgan haqiqiy xato matnini to'liq terminalga chiqaramiz,
        # shunda taxmin qilish o'rniga ANIQ sababni ko'ramiz.
        logger.error(f"Gemini API xatosi ({response.status_code}): {response.text}")
        raise RuntimeError(f"Gemini API {response.status_code}: {response.text[:300]}")

    return response.json()


class QuotaExceededError(Exception):
    """Gemini API bepul kvotasi tugaganda ko'tariladi."""
    pass


def gemini_chat(history: list, system_prompt: str, max_tokens: int = 1024) -> str:
    contents = []
    for msg in history:
        role = "user" if msg["role"] == "user" else "model"
        contents.append({"role": role, "parts": [{"text": msg["content"]}]})

    payload = {
        "contents": contents,
        "systemInstruction": {"parts": [{"text": system_prompt}]},
        "generationConfig": {"maxOutputTokens": max_tokens},
    }
    data = _call_gemini(payload)

    try:
        parts = data["candidates"][0]["content"]["parts"]
        text = "".join(p.get("text", "") for p in parts)
        return text.strip() if text.strip() else "⚠️ Gemini bo'sh javob qaytardi. Qayta urinib ko'ring."
    except (KeyError, IndexError):
        finish_reason = data.get("candidates", [{}])[0].get("finishReason", "NOMA'LUM")
        return f"⚠️ Gemini javob bera olmadi (sabab: {finish_reason})."


def gemini_vision(prompt: str, image_bytes: bytes, mime_type: str = "image/jpeg", max_tokens: int = 1024) -> str:
    image_b64 = base64.b64encode(image_bytes).decode("utf-8")
    payload = {
        "contents": [{
            "role": "user",
            "parts": [
                {"text": prompt},
                {"inline_data": {"mime_type": mime_type, "data": image_b64}},
            ],
        }],
        "generationConfig": {"maxOutputTokens": max_tokens},
    }
    data = _call_gemini(payload)

    try:
        parts = data["candidates"][0]["content"]["parts"]
        text = "".join(p.get("text", "") for p in parts)
        return text.strip() if text.strip() else "⚠️ Gemini rasmni tahlil qila olmadi."
    except (KeyError, IndexError):
        return "⚠️ Rasmni tahlil qilishda xatolik yuz berdi."


def generate_image(prompt: str) -> bytes:
    """Pollinations.ai orqali bepul rasm yaratadi (kalit talab qilinmaydi)."""
    encoded = urllib.parse.quote(prompt)
    url = f"https://image.pollinations.ai/prompt/{encoded}?width=1024&height=1024&nologo=true"
    response = requests.get(url, timeout=90)
    response.raise_for_status()
    return response.content


def extract_text_from_document(file_bytes: bytes, filename: str):
    lower = filename.lower()
    if lower.endswith(".txt"):
        return file_bytes.decode("utf-8", errors="ignore")
    if lower.endswith(".pdf"):
        try:
            from pypdf import PdfReader
            reader = PdfReader(io.BytesIO(file_bytes))
            return "\n".join((page.extract_text() or "") for page in reader.pages)
        except ImportError:
            return None
    return None


# ==========================================================
# 4) KLAVIATURALAR (Reply va Inline)
# ==========================================================
BTN_AI = "🤖 AI"
BTN_IMAGE = "🎨 Rasm yaratish"
BTN_SEARCH = "🔎 Internetdan qidirish"
BTN_DOC = "📄 Hujjat"
BTN_CHATS = "🗂 Suhbatlarim"
BTN_SETTINGS = "⚙️ Sozlamalar"
BTN_PREMIUM = "🚀 Premium"
BTN_PROFILE = "👤 Profilim"
BTN_ADMIN = "🛠 Admin panel"


def main_reply_keyboard(admin: bool):
    rows = [
        [BTN_AI, BTN_IMAGE],
        [BTN_SEARCH, BTN_DOC],
        [BTN_CHATS, BTN_SETTINGS],
        [BTN_PREMIUM, BTN_PROFILE],
    ]
    if admin:
        rows.append([BTN_ADMIN])
    return ReplyKeyboardMarkup(rows, resize_keyboard=True)


def model_choice_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("💬 Erkin suhbat", callback_data="mode_chat")],
        [InlineKeyboardButton("🧠 Chuqur fikrlash", callback_data="mode_reasoning")],
    ])


def settings_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🗑 Suhbat tarixini tozalash", callback_data="settings_clear_history")],
        [InlineKeyboardButton("ℹ️ Bot haqida", callback_data="settings_about")],
    ])


def confirm_broadcast_keyboard():
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ Ha, yuborish", callback_data="broadcast_confirm"),
        InlineKeyboardButton("❌ Bekor qilish", callback_data="broadcast_cancel"),
    ]])


def admin_panel_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("👥 Foydalanuvchilar", callback_data="admin_users"),
         InlineKeyboardButton("📊 Statistika", callback_data="admin_stats")],
        [InlineKeyboardButton("📢 Xabar yuborish", callback_data="admin_broadcast")],
        [InlineKeyboardButton("🤖 AI sozlamalari", callback_data="admin_ai_settings"),
         InlineKeyboardButton("👑 Premium foydalanuvchilar", callback_data="admin_premium")],
        [InlineKeyboardButton("🚫 Foydalanuvchini bloklash", callback_data="admin_ban")],
        [InlineKeyboardButton("📚 Kontent boshqaruvi", callback_data="admin_content"),
         InlineKeyboardButton("⚙️ Bot sozlamalari", callback_data="admin_bot_settings")],
        [InlineKeyboardButton("🔑 API sozlamalari", callback_data="admin_api_settings")],
    ])


def admin_back_keyboard():
    return InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Orqaga", callback_data="admin_back")]])


# ==========================================================
# 5) SYSTEM PROMPTLAR VA VAQTINCHALIK HOLATLAR
# ==========================================================
SYSTEM_PROMPTS = {
    "chat": (
        "Sen do'stona, foydali AI yordamchisan. Ismingiz Serius Bot. "
        "O'zbek tilida, qisqa va tushunarli javob ber. "
        "Matematik ifodalarni HECH QACHON LaTeX formatida yozma "
        "(masalan \\[ \\begin{aligned} x^{2} kabi belgilarni ishlatma), "
        "buning o'rniga oddiy matn bilan yoz (masalan x^2 yoki x*x)."
    ),
    "reasoning": (
        "Sen chuqur fikrlaydigan AI yordamchisan. Ismingiz Serius Bot. "
        "Savolni bosqichma-bosqich tahlil qilib, mantiqiy va batafsil javob ber. "
        "O'zbek tilida javob ber. LaTeX formatidan hech qachon foydalanma."
    ),
}

awaiting_image_prompt = set()
awaiting_broadcast_text = set()
pending_broadcast = {}


# ==========================================================
# 6) /start VA REPLY KEYBOARD FUNKSIYALARI
# ==========================================================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u = update.effective_user
    add_or_update_user(u.id, username=u.username, first_name=u.full_name)
    text = (
        "👋 Salom! Men Serius Bot — sizning shaxsiy AI yordamchingizman.\n"
        "Meni Behruzbek Salmonjonov yaratgan.\n\n"
        "Pastdagi menyudan bo'lim tanlang yoki menga to'g'ridan-to'g'ri savol yozing."
    )
    await update.message.reply_text(text, reply_markup=main_reply_keyboard(is_admin(u.id)))


async def handle_btn_ai(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🤖 AI rejimini tanlang:", reply_markup=model_choice_keyboard())


async def handle_btn_image(update: Update, context: ContextTypes.DEFAULT_TYPE):
    awaiting_image_prompt.add(update.effective_user.id)
    await update.message.reply_text(
        "🎨 Qanday rasm yaratamiz? Tavsifini yozing.\n(Ingliz tilida yozsangiz, natija sifatliroq bo'ladi)"
    )


async def handle_btn_search(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🔎 Internetdan qidirish bo'limi hozircha ishlab chiqilmoqda.\n"
        "Tez orada shu yerdan aniq manbalar bilan javob olasiz."
    )


async def handle_btn_doc(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "📄 Menga .txt yoki .pdf fayl yuboring — men uni o'qib, "
        "qisqacha mazmunini aytib beraman yoki savollaringizga javob beraman."
    )


async def handle_btn_chats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    history = get_chat_history(update.effective_user.id)
    await update.message.reply_text(
        f"🗂 Sizda saqlangan suhbat: {len(history)} ta xabar.\n\n"
        f"Suhbatni tozalash uchun ⚙️ Sozlamalar bo'limiga o'ting."
    )


async def handle_btn_settings(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("⚙️ Sozlamalar:", reply_markup=settings_keyboard())


async def handle_btn_premium(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = get_user(update.effective_user.id)
    if user and user["is_premium"]:
        text = "🚀 Sizda Premium faol! Cheklovsiz foydalanishingiz mumkin."
    else:
        text = (
            "🚀 Premium hali sotib olish tizimi ulanmagan.\n\n"
            "Premium bilan: yuqori limit, kuchli modellar, ko'proq rasm yaratish imkoniyati.\n"
            "Bog'lanish uchun admin bilan aloqaga chiqing."
        )
    await update.message.reply_text(text)


async def handle_btn_profile(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u = update.effective_user
    user = get_user(u.id)
    if not user:
        add_or_update_user(u.id, username=u.username, first_name=u.full_name)
        user = get_user(u.id)

    status = "🚫 Bloklangan" if user["is_banned"] else "✅ Faol"
    premium = "🚀 Ha" if user["is_premium"] else "Yo'q"
    mode = "💬 Erkin suhbat" if user["selected_mode"] == "chat" else "🧠 Chuqur fikrlash"

    text = (
        f"👤 Profil\n\n"
        f"🆔 Telegram ID: {u.id}\n"
        f"👤 Ism: {user['first_name'] or '-'}\n"
        f"📅 Ro'yxatdan o'tgan: {user['joined_at'][:10]}\n"
        f"🤖 Tanlangan model: {mode}\n"
        f"💬 Xabarlar soni: {user['message_count']}\n"
        f"🎨 Yaratilgan rasmlar: {user['image_count']}\n"
        f"🚀 Premium: {premium}\n"
        f"📶 Holat: {status}"
    )
    await update.message.reply_text(text)


async def handle_btn_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("🚫 Sizda admin paneldan foydalanish huquqi yo'q.")
        return
    await update.message.reply_text("🛠 Admin panel:", reply_markup=admin_panel_keyboard())


REPLY_BUTTON_HANDLERS = {
    BTN_AI: handle_btn_ai,
    BTN_IMAGE: handle_btn_image,
    BTN_SEARCH: handle_btn_search,
    BTN_DOC: handle_btn_doc,
    BTN_CHATS: handle_btn_chats,
    BTN_SETTINGS: handle_btn_settings,
    BTN_PREMIUM: handle_btn_premium,
    BTN_PROFILE: handle_btn_profile,
    BTN_ADMIN: handle_btn_admin,
}


# ==========================================================
# 7) INLINE TUGMALAR (model tanlash, sozlamalar)
# ==========================================================

async def inline_button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id

    if query.data == "mode_chat":
        set_mode(user_id, "chat")
        await query.edit_message_text("💬 Erkin suhbat rejimi yoqildi. Menga xohlagan narsangizni yozing!")

    elif query.data == "mode_reasoning":
        set_mode(user_id, "reasoning")
        await query.edit_message_text("🧠 Chuqur fikrlash rejimi yoqildi. Murakkab savol bering!")

    elif query.data == "settings_clear_history":
        clear_chat_history(user_id)
        await query.edit_message_text("🗑 Suhbat tarixi tozalandi.")

    elif query.data == "settings_about":
        await query.edit_message_text(
            "ℹ️ Serius Bot\n\nYaratuvchi: Behruzbek Salmonjonov\n"
            "AI: Google Gemini (chat) + Pollinations (rasm)\nVersiya: 2.0"
        )


# ==========================================================
# 8) ADMIN PANEL — TO'LIQ FUNKSIONAL
# ==========================================================

async def admin_callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = query.from_user.id

    if not is_admin(user_id):
        await query.answer("Sizda ruxsat yo'q.", show_alert=True)
        return

    await query.answer()
    data = query.data

    if data == "admin_back":
        await query.edit_message_text("🛠 Admin panel:", reply_markup=admin_panel_keyboard())
        return

    if data == "admin_users":
        users = get_all_users()[:20]
        if not users:
            text = "Hozircha foydalanuvchilar yo'q."
        else:
            lines = []
            for u in users:
                status = "🚫" if u["is_banned"] else "✅"
                prem = "🚀" if u["is_premium"] else ""
                uname = f"@{u['username']}" if u["username"] else "-"
                lines.append(f"{status}{prem} {u['telegram_id']} — {u['first_name'] or '-'} ({uname})")
            text = "👥 Foydalanuvchilar (so'nggi 20 ta):\n\n" + "\n".join(lines)
        await query.edit_message_text(text, reply_markup=admin_back_keyboard())

    elif data == "admin_stats":
        s = get_stats()
        text = (
            "📊 Statistika\n\n"
            f"👥 Jami foydalanuvchilar: {s['total_users']}\n"
            f"🟢 Faol foydalanuvchilar: {s['active_users']}\n"
            f"💬 Jami xabarlar: {s['total_messages']}\n"
            f"🎨 Jami rasm so'rovlari: {s['total_images']}\n"
            f"🚀 Premium foydalanuvchilar: {s['premium_users']}\n"
            f"🚫 Bloklangan foydalanuvchilar: {s['banned_users']}"
        )
        await query.edit_message_text(text, reply_markup=admin_back_keyboard())

    elif data == "admin_broadcast":
        awaiting_broadcast_text.add(user_id)
        await query.edit_message_text(
            "📢 Barcha foydalanuvchilarga yuboriladigan xabar matnini yozing.\nBekor qilish uchun /admin yozing."
        )

    elif data == "admin_ai_settings":
        text = (
            "🤖 AI sozlamalari\n\n"
            f"Faol model: Google Gemini ({TEXT_MODEL})\n"
            "Modelni almashtirish uchun shu faylning yuqorisidagi "
            "TEXT_MODEL qiymatini o'zgartiring va botni qayta ishga tushiring."
        )
        await query.edit_message_text(text, reply_markup=admin_back_keyboard())

    elif data == "admin_premium":
        users = [u for u in get_all_users() if u["is_premium"]]
        if not users:
            text = "👑 Hozircha premium foydalanuvchi yo'q.\n\nBerish uchun: /premium USER_ID"
        else:
            lines = [f"👑 {u['telegram_id']} — {u['first_name'] or '-'}" for u in users]
            text = "👑 Premium foydalanuvchilar:\n\n" + "\n".join(lines) + "\n\nBerish: /premium USER_ID\nOlib tashlash: /unpremium USER_ID"
        await query.edit_message_text(text, reply_markup=admin_back_keyboard())

    elif data == "admin_ban":
        await query.edit_message_text(
            "🚫 Bloklash: /ban USER_ID\n✅ Blokdan chiqarish: /unban USER_ID\n\n"
            "USER_ID'ni 👥 Foydalanuvchilar bo'limidan olishingiz mumkin.",
            reply_markup=admin_back_keyboard()
        )

    elif data == "admin_content":
        await query.edit_message_text(
            "📚 Kontent boshqaruvi\n\n"
            "Barcha foydalanuvchilarning suhbat tarixini butunlay tozalash uchun:\n/clearall\n\n"
            "⚠️ Bu amalni ortga qaytarib bo'lmaydi.",
            reply_markup=admin_back_keyboard()
        )

    elif data == "admin_bot_settings":
        maintenance = get_setting("maintenance_mode", "0")
        state_text = "🔴 Yoqilgan" if maintenance == "1" else "🟢 O'chirilgan"
        await query.edit_message_text(
            f"⚙️ Bot sozlamalari\n\nTexnik ishlar rejimi: {state_text}\n\n"
            f"Yoqish: /maintenance on\nO'chirish: /maintenance off",
            reply_markup=admin_back_keyboard()
        )

    elif data == "admin_api_settings":
        masked_gemini = GEMINI_API_KEY[:8] + "..." + GEMINI_API_KEY[-4:] if len(GEMINI_API_KEY) > 12 else "****"
        masked_token = BOT_TOKEN[:8] + "..." if len(BOT_TOKEN) > 8 else "****"
        await query.edit_message_text(
            f"🔑 API sozlamalari\n\nBOT_TOKEN: {masked_token}\nGEMINI_API_KEY: {masked_gemini}\n\n"
            "Xavfsizlik uchun kalitlarni faqat .env fayl orqali qo'lda o'zgartiring va botni qayta ishga tushiring.",
            reply_markup=admin_back_keyboard()
        )


async def broadcast_confirm_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    admin_id = query.from_user.id

    if not is_admin(admin_id):
        await query.answer("Sizda ruxsat yo'q.", show_alert=True)
        return

    await query.answer()

    if query.data == "broadcast_cancel":
        pending_broadcast.pop(admin_id, None)
        await query.edit_message_text("❌ Xabar yuborish bekor qilindi.")
        return

    if query.data == "broadcast_confirm":
        text = pending_broadcast.pop(admin_id, None)
        if not text:
            await query.edit_message_text("⚠️ Yuboriladigan xabar topilmadi.")
            return
        users = get_all_users()
        sent = 0
        for u in users:
            try:
                await context.bot.send_message(chat_id=u["telegram_id"], text=f"📢 E'lon:\n\n{text}")
                sent += 1
            except Exception:
                pass
        await query.edit_message_text(f"✅ Xabar {sent} ta foydalanuvchiga yuborildi.")


# ==========================================================
# 9) ADMIN KOMANDALAR
# ==========================================================

async def cmd_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("🚫 Sizda admin paneldan foydalanish huquqi yo'q.")
        return
    await update.message.reply_text("🛠 Admin panel:", reply_markup=admin_panel_keyboard())


async def cmd_ban(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("🚫 Sizda bu buyruqdan foydalanish huquqi yo'q.")
        return
    if not context.args or not context.args[0].isdigit():
        await update.message.reply_text("Foydalanish: /ban USER_ID")
        return
    uid = int(context.args[0])
    set_banned(uid, True)
    await update.message.reply_text(f"🚫 Foydalanuvchi {uid} bloklandi.")


async def cmd_unban(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("🚫 Sizda bu buyruqdan foydalanish huquqi yo'q.")
        return
    if not context.args or not context.args[0].isdigit():
        await update.message.reply_text("Foydalanish: /unban USER_ID")
        return
    uid = int(context.args[0])
    set_banned(uid, False)
    await update.message.reply_text(f"✅ Foydalanuvchi {uid} blokdan chiqarildi.")


async def cmd_premium(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("🚫 Sizda bu buyruqdan foydalanish huquqi yo'q.")
        return
    if not context.args or not context.args[0].isdigit():
        await update.message.reply_text("Foydalanish: /premium USER_ID")
        return
    uid = int(context.args[0])
    set_premium(uid, True)
    await update.message.reply_text(f"👑 Foydalanuvchi {uid} endi Premium.")


async def cmd_unpremium(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("🚫 Sizda bu buyruqdan foydalanish huquqi yo'q.")
        return
    if not context.args or not context.args[0].isdigit():
        await update.message.reply_text("Foydalanish: /unpremium USER_ID")
        return
    uid = int(context.args[0])
    set_premium(uid, False)
    await update.message.reply_text(f"Foydalanuvchi {uid} uchun Premium bekor qilindi.")


async def cmd_maintenance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("🚫 Sizda bu buyruqdan foydalanish huquqi yo'q.")
        return
    if not context.args or context.args[0] not in ("on", "off"):
        await update.message.reply_text("Foydalanish: /maintenance on  yoki  /maintenance off")
        return
    set_setting("maintenance_mode", "1" if context.args[0] == "on" else "0")
    await update.message.reply_text(f"⚙️ Texnik ishlar rejimi: {'yoqildi' if context.args[0]=='on' else 'o‘chirildi'}")


async def cmd_clearall(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("🚫 Sizda bu buyruqdan foydalanish huquqi yo'q.")
        return
    clear_all_chat_histories()
    await update.message.reply_text("🗑 Barcha foydalanuvchilarning suhbat tarixi tozalandi.")


# ==========================================================
# 10) ODDIY MATN XABARLAR — reply tugmalar, keyingi bosqichlar, AI chat
# ==========================================================

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u = update.effective_user
    user_id = u.id
    text = update.message.text

    add_or_update_user(user_id, username=u.username, first_name=u.full_name)
    user = get_user(user_id)

    if user["is_banned"]:
        await update.message.reply_text("⛔ Siz botdan foydalanish huquqidan mahrum qilingansiz.")
        return

    if get_setting("maintenance_mode", "0") == "1" and not is_admin(user_id):
        await update.message.reply_text("🛠 Bot hozir texnik ishlar tufayli vaqtincha ishlamayapti. Birozdan so'ng qayta urinib ko'ring.")
        return

    if text in REPLY_BUTTON_HANDLERS:
        await REPLY_BUTTON_HANDLERS[text](update, context)
        return

    if user_id in awaiting_broadcast_text:
        awaiting_broadcast_text.discard(user_id)
        pending_broadcast[user_id] = text
        preview = text if len(text) < 300 else text[:300] + "..."
        await update.message.reply_text(
            f"📢 Quyidagi xabar barcha foydalanuvchilarga yuborilsinmi?\n\n{preview}",
            reply_markup=confirm_broadcast_keyboard()
        )
        return

    if user_id in awaiting_image_prompt:
        awaiting_image_prompt.discard(user_id)
        await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="upload_photo")
        try:
            image_bytes = generate_image(text)
            increment_image_count(user_id)
            await update.message.reply_photo(photo=image_bytes, caption=f"🎨 {text}")
        except Exception as e:
            logger.error(f"Rasm yaratishda xatolik: {e}")
            await update.message.reply_text("⚠️ Rasm yaratishda xatolik yuz berdi. Qayta urinib ko'ring.")
        return

    increment_message_count(user_id)
    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")

    mode = user["selected_mode"] or "chat"
    system_prompt = SYSTEM_PROMPTS.get(mode, SYSTEM_PROMPTS["chat"])

    history = get_chat_history(user_id)
    history.append({"role": "user", "content": text})

    try:
        reply = gemini_chat(history, system_prompt)
        history.append({"role": "assistant", "content": reply})
        save_chat_history(user_id, history)
        await update.message.reply_text(reply)
    except QuotaExceededError:
        await update.message.reply_text(
            "⚠️ Bugungi bepul AI so'rov limiti tugadi. Ertaga qayta urinib ko'ring yoki admin bilan bog'laning."
        )
    except requests.exceptions.Timeout:
        logger.error("Gemini xatosi: vaqt tugadi (internet sekin bo'lishi mumkin)")
        await update.message.reply_text(
            "⚠️ Javob kutish vaqti tugadi — internet aloqangiz sekin bo'lishi mumkin. "
            "Wi-Fi'ga ulanib yoki qayta urinib ko'ring."
        )
    except Exception as e:
        logger.error(f"Gemini xatosi: {e}")
        await update.message.reply_text("⚠️ Kechirasiz, javob berishda xatolik yuz berdi. Birozdan so'ng qayta urinib ko'ring.")


# ==========================================================
# 11) RASM VA HUJJAT YUBORILGANDA
# ==========================================================

async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u = update.effective_user
    user = get_user(u.id)
    if user and user["is_banned"]:
        await update.message.reply_text("⛔ Siz botdan foydalanish huquqidan mahrum qilingansiz.")
        return

    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")

    try:
        photo = update.message.photo[-1]
        file = await context.bot.get_file(photo.file_id)
        photo_bytes = await file.download_as_bytearray()

        caption = update.message.caption or "Bu rasmda nima bor? Batafsil, o'zbek tilida tasvirlab ber."
        reply = gemini_vision(caption, bytes(photo_bytes))
        await update.message.reply_text(reply)
        increment_message_count(u.id)

    except Exception as e:
        logger.error(f"Rasm tahlilida xatolik: {e}")
        await update.message.reply_text("⚠️ Rasmni tahlil qilishda xatolik yuz berdi.")


async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u = update.effective_user
    user = get_user(u.id)
    if user and user["is_banned"]:
        await update.message.reply_text("⛔ Siz botdan foydalanish huquqidan mahrum qilingansiz.")
        return

    doc = update.message.document
    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")

    try:
        file = await context.bot.get_file(doc.file_id)
        file_bytes = await file.download_as_bytearray()
        content = extract_text_from_document(bytes(file_bytes), doc.file_name)

        if content is None:
            await update.message.reply_text("⚠️ Hozircha faqat .txt va .pdf fayllarni o'qiy olaman.")
            return
        if not content.strip():
            await update.message.reply_text("⚠️ Fayldan matn topilmadi (rasm sifatida skanerlangan bo'lishi mumkin).")
            return

        trimmed = content[:4000]
        prompt = f"Quyidagi hujjat matnini o'zbek tilida qisqacha (5-8 gapda) mazmunini tushuntirib ber:\n\n{trimmed}"
        reply = gemini_chat([{"role": "user", "content": prompt}], SYSTEM_PROMPTS["chat"])
        await update.message.reply_text(f"📄 Hujjat mazmuni:\n\n{reply}")
        increment_message_count(u.id)

    except QuotaExceededError:
        await update.message.reply_text(
            "⚠️ Bugungi bepul AI so'rov limiti tugadi. Ertaga qayta urinib ko'ring yoki admin bilan bog'laning."
        )
    except requests.exceptions.Timeout:
        logger.error("Hujjat tahlilida xatolik: vaqt tugadi (internet sekin bo'lishi mumkin)")
        await update.message.reply_text(
            "⚠️ Javob kutish vaqti tugadi — internet aloqangiz sekin bo'lishi mumkin. "
            "Wi-Fi'ga ulanib yoki qayta urinib ko'ring."
        )
    except Exception as e:
        logger.error(f"Hujjat tahlilida xatolik: {e}")
        await update.message.reply_text("⚠️ Hujjatni o'qishda xatolik yuz berdi.")


# ==========================================================
# 12) BOTNI ISHGA TUSHIRISH
# ==========================================================

def main():
    init_db()

    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("admin", cmd_admin))
    app.add_handler(CommandHandler("ban", cmd_ban))
    app.add_handler(CommandHandler("unban", cmd_unban))
    app.add_handler(CommandHandler("premium", cmd_premium))
    app.add_handler(CommandHandler("unpremium", cmd_unpremium))
    app.add_handler(CommandHandler("maintenance", cmd_maintenance))
    app.add_handler(CommandHandler("clearall", cmd_clearall))

    app.add_handler(CallbackQueryHandler(admin_callback_handler, pattern="^admin_"))
    app.add_handler(CallbackQueryHandler(broadcast_confirm_handler, pattern="^broadcast_"))
    app.add_handler(CallbackQueryHandler(inline_button_handler))

    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    app.add_handler(MessageHandler(filters.Document.ALL, handle_document))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    print("✅ Serius Bot ishga tushdi... To'xtatish uchun Ctrl+C bosing.")
    app.run_polling()


if __name__ == "__main__":
    main()

