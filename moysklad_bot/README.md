# MoySklad Telegram bot

Kichik biznes uchun Telegram bot: MoySklad'dan hisobotlarni to'g'ridan-to'g'ri Telegram'ga olib keladi.

## Imkoniyatlar

- 📊 **Umumiy hisobot** — savdo, kassa kirim/chiqim, kassa balansi, foyda va sof foyda
- 📦 **Sotuv tovarlar bo'yicha** — tanlangan davrda eng ko'p sotilgan tovarlar
- 📁 **Tovar qoldiqlari** — papkalar bo'yicha qoldiq soni va qiymati (hozirgi yoki tanlangan sana bo'yicha)
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

> ⚠️ Tokenlarni hech qachon kod ichiga yozmang va GitHub'ga yuklamang.
