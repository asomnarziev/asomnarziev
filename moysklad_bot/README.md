# MoySklad Telegram bot

Kichik biznes uchun Telegram bot: MoySklad'dan hisobotlarni to'g'ridan-to'g'ri Telegram'ga olib keladi.

## Imkoniyatlar

- 📊 **Umumiy hisobot** — savdo, kassa kirim/chiqim, chakana savdo (naqd, karta, qarz) — hammasi dollarda, MoySklad hujjatidagi kurs bo'yicha va foyda (MoySklad "Прибыли и убытки" dagi операционная прибыль)
- 📅 **Davr tanlash** — tayyor davrlar (bugun, kecha, shu/o'tgan hafta, shu/o'tgan oy) yoki kalendarda boshlanish va tugash kunini bosish
- 📦 **Sotuv tovarlar bo'yicha** — tanlangan davrda eng ko'p sotilgan tovarlar (tayyor davr yoki kalendar)
- 🏆 **Top tovarlar** — Top 10 / 20 / 30: sotilgan soni bo'yicha eng ko'p va eng kam sotilgan tovarlar; 🐌 qoldig'i bor, lekin oxirgi prixoddan beri sotilmagan tovarlar
- 📁 **Tovar qoldiqlari** — papkalar bo'yicha qoldiq soni va qiymati (hozirgi yoki tanlangan sana bo'yicha)
- 🔄 **Aylanma** — MoySklad "Обороты": davr boshidagi, kirim, chiqim va oxiridagi soni/summasi, bosh guruhlar bo'yicha; solishtirish: kunlik, haftalik, oylik (bir xil kunlar) yoki kalendardan ikki davr; oylar bo'yicha ko'rinish (3/6/12 oy yoki istalgan oraliq, soni va summasi)
- 🔎 **Tovar qidirish** — chatga tovar nomi (yoki artikul/kodi) yozilsa, o'xshash tovarlar qoldig'i chiqadi; xato yozilgan nomlarni ham topadi, kirill va lotinda yozilganini farqlamaydi (холодильник = xolodilnik = holodilnik), hech narsa bo'lmasa "topilmadi" deydi
- 🔐 **Ruxsat tizimi** — yangi foydalanuvchi `/start` bosganda admin tasdiqlaydi yoki rad etadi

## O'rnatish

```bash
cd moysklad_bot
python -m venv venv
source venv/bin/activate          # Windows: venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env              # keyin .env ichidagi qiymatlarni to'ldiring
python bot.py
```

## Sozlamalar (`.env`)

| O'zgaruvchi | Tavsif |
|---|---|
| `TELEGRAM_BOT_TOKEN` | [@BotFather](https://t.me/BotFather) bergan token |
| `MOYSKLAD_TOKEN` | MoySklad API tokeni |
| `ADMIN_ID` | Adminning Telegram ID raqami |
| `TIMEZONE` | Ixtiyoriy, standart `Asia/Tashkent` — "Bugun"/"Kecha" shu vaqt bo'yicha |

> ⚠️ Tokenlarni hech qachon kod ichiga yozmang va GitHub'ga yuklamang.
