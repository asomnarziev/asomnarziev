import os
import json
import html
import time
import difflib
import datetime as dt

import requests
import telebot
from telebot import types
from dotenv import load_dotenv
from telegram_bot_calendar import DetailedTelegramCalendar

# --- SOZLAMALAR ---
# Tokenlar kodda emas, .env faylida saqlanadi (.env.example ga qarang)
load_dotenv()
MOYSKLAD_TOKEN = os.environ["MOYSKLAD_TOKEN"]
TELEGRAM_BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
MOYSKLAD_API = "https://api.moysklad.ru/api/remap/1.2"

# --- ADMIN VA RUXSATLAR ---
ADMIN_ID = int(os.environ["ADMIN_ID"])
USERS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "allowed_users.json")


def load_allowed_users():
    try:
        with open(USERS_FILE, encoding="utf-8") as f:
            return set(json.load(f)) | {ADMIN_ID}
    except (FileNotFoundError, ValueError):
        return {ADMIN_ID}


def save_allowed_users():
    with open(USERS_FILE, "w", encoding="utf-8") as f:
        json.dump(sorted(ALLOWED_USERS), f)


# Ruxsat berilganlar faylda saqlanadi, bot qayta ishga tushsa ham yo'qolmaydi
ALLOWED_USERS = load_allowed_users()

bot = telebot.TeleBot(TELEGRAM_BOT_TOKEN, parse_mode="HTML")
headers = {"Authorization": f"Bearer {MOYSKLAD_TOKEN}", "Content-Type": "application/json"}
user_steps = {}


# --- YORDAMCHI FUNKSIYALAR ---
def esc(text):
    """Tovar nomlaridagi <, >, & belgilari HTML xabarni buzmasligi uchun."""
    return html.escape(str(text))


def ms_rows(path, params=None):
    """MoySklad'dan barcha qatorlarni oladi (1000 tadan ko'p bo'lsa sahifalab)."""
    rows, offset = [], 0
    while True:
        p = dict(params or {}, limit=1000, offset=offset)
        r = requests.get(MOYSKLAD_API + path, headers=headers, params=p, timeout=60)
        r.raise_for_status()
        data = r.json()
        batch = data.get('rows', [])
        rows += batch
        offset += len(batch)
        if not batch or offset >= data.get('meta', {}).get('size', 0):
            return rows


def send_long(chat_id, text):
    """Telegram 4096 belgidan uzun xabarni qabul qilmaydi - qatorlar bo'yicha bo'lib yuboramiz."""
    chunk = ""
    for line in text.split("\n"):
        if len(chunk) + len(line) + 1 > 4000:
            bot.send_message(chat_id, chunk)
            chunk = ""
        chunk += line + "\n"
    if chunk.strip():
        bot.send_message(chat_id, chunk)


# --- ASOSIY MENYU ---
def main_menu(chat_id):
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True)
    markup.add(types.KeyboardButton("📊 Umumiy Hisobot"), types.KeyboardButton("📦 Sotuv Tovarlar Bo'yicha"))
    markup.add(types.KeyboardButton("📁 Tovar Qoldiqlari"))
    bot.send_message(chat_id, "👋 Bo'limni tanlang:", reply_markup=markup)


@bot.message_handler(commands=['start'])
def start(message):
    uid = message.from_user.id
    if uid in ALLOWED_USERS:
        user_steps[message.chat.id] = {}
        main_menu(message.chat.id)
    else:
        username = f"@{message.from_user.username}" if message.from_user.username else "username yo'q"
        info = f"👤 Foydalanuvchi: {esc(message.from_user.first_name)} ({esc(username)})\nID: <code>{uid}</code>"
        markup = types.InlineKeyboardMarkup()
        markup.add(types.InlineKeyboardButton("✅ Ruxsat berish", callback_data=f"auth_allow_{uid}"),
                   types.InlineKeyboardButton("❌ Rad etish", callback_data=f"auth_deny_{uid}"))
        try:
            bot.send_message(ADMIN_ID, f"🔔 <b>Yangi kirish so'rovi:</b>\n{info}", reply_markup=markup)
            bot.send_message(message.chat.id, "⏳ Kirish uchun so'rov yuborildi. Admin tasdiqlashini kuting...")
        except telebot.apihelper.ApiTelegramException:
            bot.send_message(message.chat.id, "⚠️ Admin botni ishga tushirishi kerak.")


@bot.callback_query_handler(func=lambda call: True)
def handle_callbacks(call):
    # Tugmadagi "soat" belgisini o'chiradi
    try:
        bot.answer_callback_query(call.id)
    except telebot.apihelper.ApiTelegramException:
        pass
    try:
        dispatch_callback(call)
    except requests.RequestException as e:
        bot.send_message(call.message.chat.id, f"⚠️ MoySklad bilan bog'lanishda xato:\n<code>{esc(e)}</code>")


def dispatch_callback(call):
    chat_id = call.message.chat.id
    uid = call.from_user.id

    if call.data.startswith("auth_"):
        if uid != ADMIN_ID: return
        action, target_id = call.data.split("_")[1], int(call.data.split("_")[2])
        if action == "allow":
            ALLOWED_USERS.add(target_id)
            save_allowed_users()
            bot.send_message(target_id, "✅ Admin ruxsat berdi! /start bosing.")
            bot.edit_message_text(f"✅ {target_id} ga ruxsat berildi.", chat_id, call.message.message_id)
        else:
            bot.send_message(target_id, "❌ So'rovingiz rad etildi.")
            bot.edit_message_text(f"❌ {target_id} rad etildi.", chat_id, call.message.message_id)
        return

    if uid not in ALLOWED_USERS: return
    if chat_id not in user_steps: user_steps[chat_id] = {}

    if call.data in ["date_calendar", "fdate_calendar"]:
        user_steps[chat_id]['cal_mode'] = 'stock' if call.data == "fdate_calendar" else 'sales'
        calendar, step = DetailedTelegramCalendar(calendar_id=1).build()
        bot.edit_message_text(f"📅 Sana tanlang: {step}", chat_id, call.message.message_id, reply_markup=calendar)

    elif DetailedTelegramCalendar.func(calendar_id=1)(call):
        res, key, step = DetailedTelegramCalendar(calendar_id=1).process(call.data)
        if not res and key:
            bot.edit_message_text(f"📅 Tanlang: {step}", chat_id, call.message.message_id, reply_markup=key)
        elif res:
            if user_steps[chat_id].get('cal_mode') == 'stock':
                user_steps[chat_id].update({'moment': res.strftime("%Y-%m-%d 23:59:59"), 'label': res.strftime("%d.%m.%Y")})
                show_stock_folders(chat_id, message_id=call.message.message_id)
            else:
                user_steps[chat_id]['start_date'] = res
                calendar, step = DetailedTelegramCalendar(calendar_id=2).build()
                bot.edit_message_text(f"✅ Boshlanish: {res.strftime('%d.%m.%Y')}\n📅 Tugashni tanlang:", chat_id, call.message.message_id, reply_markup=calendar)

    elif DetailedTelegramCalendar.func(calendar_id=2)(call):
        res, key, step = DetailedTelegramCalendar(calendar_id=2).process(call.data)
        if not res and key:
            bot.edit_message_text(f"📅 Tanlang: {step}", chat_id, call.message.message_id, reply_markup=key)
        elif res:
            s_dt = user_steps[chat_id].get('start_date')
            if not s_dt:
                # Bot qayta ishga tushgan bo'lsa, boshlanish sanasi yo'qolgan bo'ladi
                bot.send_message(chat_id, "⚠️ Boshlanish sanasi topilmadi, hisobotni qaytadan tanlang.")
                return
            process_reports(chat_id, s_dt.strftime("%Y-%m-%d 00:00:00"), res.strftime("%Y-%m-%d 23:59:59"), f"{s_dt.strftime('%d.%m.%Y')} - {res.strftime('%d.%m.%Y')}")

    elif call.data == "fdate_now":
        user_steps[chat_id].update({'moment': None, 'label': "Hozirgi"})
        show_stock_folders(chat_id, message_id=call.message.message_id)
    elif call.data == "back_root": show_stock_folders(chat_id, None, call.message.message_id)
    elif call.data.startswith('nav_'): show_stock_folders(chat_id, call.data.split('_')[1], call.message.message_id)
    elif call.data.startswith('fcalc_'): calculate_folder_stock(call)
    elif call.data.startswith('date_'): handle_quick_dates(call)


# --- HISOBOTLAR ---
def process_reports(chat_id, s, e, l):
    rtype = user_steps[chat_id].get('report_type')
    if rtype == "📦 Sotuv Tovarlar Bo'yicha":
        generate_product_sales_report(chat_id, s, e, l)
    else:
        generate_final_summary(chat_id, s, e, l)


def generate_product_sales_report(chat_id, s, e, l):
    res = ms_rows("/report/profit/byvariant", {"momentFrom": s, "momentTo": e})
    if not res:
        bot.send_message(chat_id, f"📦 <b>{l}</b> davrida sotuvlar topilmadi.")
        return
    # Eng ko'p sotilganlar birinchi
    res.sort(key=lambda r: r.get('sellSum', 0), reverse=True)
    txt = f"📦 <b>SOTUV TOVARLAR ({l})</b>\n\n"
    for r in res[:25]:
        name = r.get('variantName') or r.get('name')
        if not name: name = r.get('assortment', {}).get('name', "Noma'lum tovar")
        txt += f"🔹 {esc(name[:30])}\n    └ {r.get('sellQuantity', 0):,.0f} ta | ${r.get('sellSum', 0)/100:,.2f}\n"
    if len(res) > 25:
        txt += f"\n... va yana {len(res) - 25} ta tovar"
    send_long(chat_id, txt)


def generate_final_summary(chat_id, s, e, l):
    # 1. Savdo va Foyda
    p_res = ms_rows("/report/profit/byvariant", {"momentFrom": s, "momentTo": e})
    total_s = sum(r.get('sellSum', 0) for r in p_res) / 100
    raw_p = sum(r.get('profit', 0) for r in p_res) / 100

    # 2. Kassa Orderlari (Kirim va Chiqim)
    period = {"filter": f"moment>={s};moment<={e}"}
    total_cashin = sum(r.get('sum', 0) for r in ms_rows("/entity/cashin", period)) / 100
    total_cashout = sum(r.get('sum', 0) for r in ms_rows("/entity/cashout", period)) / 100

    # 3. Kassa Balansi (Faqat kassa orderlari farqi)
    kassa_balance = total_cashin - total_cashout

    report = (f"🗓 <b>UMUMIY HISOBOT: {l}</b>\n"
              f"━━━━━━━━━━━━━━━━━━━━\n"
              f"💰 SAVDO (Umumiy): ${total_s:,.2f}\n"
              f"📥 KIRIM PULLAR: ${total_cashin:,.2f}\n"
              f"📉 RASXODLAR: ${total_cashout:,.2f}\n"
              f"🏦 <b>KASSA (REAL): ${kassa_balance:,.2f}</b>\n"
              f"━━━━━━━━━━━━━━━━━━━━\n"
              f"💸 FOYDA: ${raw_p:,.2f}\n"
              f"✅ <b>SOF FOYDA: ${raw_p - total_cashout:,.2f}</b>")

    bot.send_message(chat_id, report)


# --- QOLDIQLAR ---
def parent_folder_id(folder):
    return folder.get('productFolder', {}).get('meta', {}).get('href', '').split('/')[-1]


def show_stock_folders(chat_id, parent_id=None, message_id=None):
    all_f = ms_rows("/entity/productfolder")
    curr = [f for f in all_f if parent_folder_id(f) == parent_id] if parent_id else [f for f in all_f if 'productFolder' not in f]
    markup = types.InlineKeyboardMarkup(row_width=1)
    for f in curr:
        f_id = f['id']
        has_sub = any(parent_folder_id(sf) == f_id for sf in all_f)
        markup.add(types.InlineKeyboardButton(text=f"📁 {f['name']}", callback_data=f"{'nav' if has_sub else 'fcalc'}_{f_id}"))
    if parent_id:
        markup.add(types.InlineKeyboardButton(text="✅ Hisoblash", callback_data=f"fcalc_{parent_id}"),
                   types.InlineKeyboardButton(text="⬅️ Orqaga", callback_data="back_root"))
    title = "📂 Bo'limni tanlang:"
    if message_id: bot.edit_message_text(title, chat_id, message_id, reply_markup=markup)
    else: bot.send_message(chat_id, title, reply_markup=markup)


def calculate_folder_stock(call):
    chat_id, f_id = call.message.chat.id, call.data.split('_')[1]
    moment = user_steps[chat_id].get('moment')
    params = {"filter": f"productFolder={MOYSKLAD_API}/entity/productfolder/{f_id}", "includeEmpty": "true"}
    if moment: params["moment"] = moment
    data = ms_rows("/report/stock/all", params)
    qty, total, details = 0, 0, ""
    for r in data:
        stock = r.get('stock', 0)
        if stock > 0:
            price = (r.get('price', 0) or 0) / 100
            qty += stock
            total += (stock * price)
            name = r.get('name', "Noma'lum")[:25]
            details += f"🔹 {esc(name)}: {stock:,.0f} ta | ${stock*price:,.2f}\n"
    label = user_steps[chat_id].get('label', 'Hozirgi')
    send_long(chat_id, f"📋 <b>QOLDIQLAR ({label})</b>\n\n{details}\n🔢 Jami: {qty:,.0f} ta\n💰 Qiymati: ${total:,.2f}")


@bot.message_handler(func=lambda m: m.from_user.id in ALLOWED_USERS and m.text in ["📊 Umumiy Hisobot", "📦 Sotuv Tovarlar Bo'yicha"])
def sales_init(message):
    user_steps[message.chat.id] = {'report_type': message.text}
    markup = types.InlineKeyboardMarkup(row_width=2)
    markup.add(types.InlineKeyboardButton("Bugun", callback_data="date_today"),
               types.InlineKeyboardButton("Kecha", callback_data="date_yesterday"),
               types.InlineKeyboardButton("📅 Kalendar", callback_data="date_calendar"))
    bot.send_message(message.chat.id, f"{message.text} davrini tanlang:", reply_markup=markup)


@bot.message_handler(func=lambda m: m.from_user.id in ALLOWED_USERS and m.text == "📁 Tovar Qoldiqlari")
def stock_report_init(message):
    user_steps[message.chat.id] = {'report_type': 'stock'}
    markup = types.InlineKeyboardMarkup()
    markup.add(types.InlineKeyboardButton("Hozirgi holat", callback_data="fdate_now"),
               types.InlineKeyboardButton("📅 Sana tanlash", callback_data="fdate_calendar"))
    bot.send_message(message.chat.id, "Vaqtni tanlang:", reply_markup=markup)


def handle_quick_dates(call):
    now = dt.datetime.now()
    if "today" in call.data:
        s, e, l = now.strftime("%Y-%m-%d 00:00:00"), now.strftime("%Y-%m-%d 23:59:59"), "BUGUN"
    else:
        y = now - dt.timedelta(days=1)
        s, e, l = y.strftime("%Y-%m-%d 00:00:00"), y.strftime("%Y-%m-%d 23:59:59"), "KECHA"
    process_reports(call.message.chat.id, s, e, l)


# --- TOVAR QIDIRISH ---
MENU_BUTTONS = {"📊 Umumiy Hisobot", "📦 Sotuv Tovarlar Bo'yicha", "📁 Tovar Qoldiqlari"}
STOCK_CACHE_SECONDS = 60
_stock_cache = {"time": 0, "rows": []}


def get_all_stock():
    """Barcha tovarlar qoldig'i. Har bir xabarda MoySklad'ga qayta so'rov yubormaslik uchun 1 daqiqa saqlanadi."""
    if time.time() - _stock_cache["time"] > STOCK_CACHE_SECONDS:
        _stock_cache["rows"] = ms_rows("/report/stock/all", {"includeEmpty": "true"})
        _stock_cache["time"] = time.time()
    return _stock_cache["rows"]


# Kirill (rus va o'zbek) harflarini lotinga o'giramiz, shunda "Холодильник" ham "xolodilnik" deb topiladi
CYR_TO_LAT = {
    'а': 'a', 'б': 'b', 'в': 'v', 'г': 'g', 'д': 'd', 'е': 'e', 'ё': 'yo', 'ж': 'j', 'з': 'z',
    'и': 'i', 'й': 'y', 'к': 'k', 'л': 'l', 'м': 'm', 'н': 'n', 'о': 'o', 'п': 'p', 'р': 'r',
    'с': 's', 'т': 't', 'у': 'u', 'ф': 'f', 'х': 'x', 'ц': 'ts', 'ч': 'ch', 'ш': 'sh', 'щ': 'sh',
    'ъ': '', 'ы': 'i', 'ь': '', 'э': 'e', 'ю': 'yu', 'я': 'ya',
    'ў': 'o', 'қ': 'q', 'ғ': 'g', 'ҳ': 'h',
}


def normalize(text):
    """Qidiruv uchun matnni bir xil ko'rinishga keltiradi (faqat solishtirish uchun, foydalanuvchiga ko'rsatilmaydi):
    kichik harf, kirill -> lotin, apostroflarsiz (qo'l = qol), h = x (holodilnik = xolodilnik)."""
    text = "".join(CYR_TO_LAT.get(ch, ch) for ch in str(text or "").lower())
    for ch in "'‘’ʻʼ`´":
        text = text.replace(ch, "")
    text = text.replace("h", "x")
    return " ".join(text.split())


def find_products(query, rows):
    """Avval nomida (yoki artikul/kodida) so'rovdagi barcha so'zlar borlarini qidiradi.
    Hech narsa topilmasa, xato yozilgan so'zlarga yaqin nomlarni qidiradi."""
    q = normalize(query)
    words = q.split()
    exact = []
    for r in rows:
        name = normalize(r.get('name'))
        text = f"{name} {normalize(r.get('article'))} {normalize(r.get('code'))}"
        if all(w in text for w in words):
            exact.append((0 if name.startswith(q) else 1, name, r))
    if exact:
        return [r for *_, r in sorted(exact, key=lambda x: x[:2])], True

    fuzzy = []
    for r in rows:
        name = normalize(r.get('name'))
        name_words = name.split()
        if not name_words:
            continue
        # Har bir so'z uchun nomdagi eng o'xshash so'zni topamiz
        score = sum(max(difflib.SequenceMatcher(None, w, nw).ratio() for nw in name_words) for w in words) / len(words)
        score = max(score, difflib.SequenceMatcher(None, q, name).ratio())
        if score >= 0.75:
            fuzzy.append((-score, name, r))
    return [r for *_, r in sorted(fuzzy, key=lambda x: x[:2])], False


@bot.message_handler(func=lambda m: m.from_user.id in ALLOWED_USERS and m.text and not m.text.startswith("/") and m.text not in MENU_BUTTONS)
def product_search(message):
    query = message.text.strip()
    if len(query) < 2:
        bot.send_message(message.chat.id, "🔎 Kamida 2 ta harf yozing.")
        return
    try:
        rows = get_all_stock()
    except requests.RequestException as e:
        bot.send_message(message.chat.id, f"⚠️ MoySklad bilan bog'lanishda xato:\n<code>{esc(e)}</code>")
        return
    found, is_exact = find_products(query, rows)
    if not found:
        bot.send_message(message.chat.id, f"❌ <b>{esc(query)}</b> — topilmadi.")
        return
    title = "🔎 Natijalar" if is_exact else "🔎 Aniq topilmadi, o'xshash tovarlar"
    txt = f"{title}: <b>{esc(query)}</b>\n\n"
    for r in found[:20]:
        name = r.get('name') or "Noma'lum"
        txt += f"🔹 {esc(name)}\n    └ Qoldiq: <b>{r.get('stock', 0):,.0f}</b> ta\n"
    if len(found) > 20:
        txt += f"\n... va yana {len(found) - 20} ta. Aniqroq yozing."
    send_long(message.chat.id, txt)


if __name__ == "__main__":
    print("Bot ishga tushdi...")
    bot.infinity_polling()
