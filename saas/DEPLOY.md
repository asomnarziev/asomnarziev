# Deploy (Docker'siz, Ubuntu/Debian VPS)

Kerak: server (root), domen (A-yozuv server IP'siga), 80/443 ochiq, Telegram bot tokeni (@BotFather).

## 0. DNS
`pbx.atcpbx.uz` uchun A-yozuv server IP'siga ko'rsatsin (`dig +short pbx.atcpbx.uz`), keyin davom eting.

## 1. Kod va sozlamalar
```bash
git clone https://github.com/asomnarziev/asomnarziev.git /opt/pbxbot
cd /opt/pbxbot && git checkout claude/festive-newton-wd3kc4   # PR merge qilinmaguncha
cp saas/.env.example saas/.env && nano saas/.env
```
`.env` da (Docker'siz variant uchun):
- `DATABASE_URL=postgresql+psycopg2://pbxbot:<PAROL>@localhost/pbxbot`
- `BASE_URL=https://pbx.atcpbx.uz`, `BOT_TOKEN`, `BOT_USERNAME`
- `SECRET_KEY`, `TG_WEBHOOK_SECRET`, parol: `openssl rand -hex 32` bilan yarating.
  `SECRET_KEY` ni yo'qotmang — OnlinePBX kalitlari shu bilan shifrlangan.
- `DOMAIN` va `POSTGRES_PASSWORD` faqat Docker uchun, kerak emas.

## 2. O'rnatish (Postgres, venv, systemd)
```bash
bash /opt/pbxbot/saas/deploy/install.sh
systemctl status pbxbot
```

## 3. HTTPS (nginx + certbot)
```bash
cp /opt/pbxbot/saas/deploy/nginx.conf /etc/nginx/sites-available/pbxbot   # server_name ni o'zgartiring
ln -s /etc/nginx/sites-available/pbxbot /etc/nginx/sites-enabled/ && nginx -t && systemctl reload nginx
certbot --nginx -d pbx.atcpbx.uz
```

## 4. Telegram webhook
```bash
cd /opt/pbxbot/saas && set -a && . ./.env && set +a && /opt/pbxbot/venv/bin/python -m app.setup_webhook
```
`{"ok": true, ...}` chiqishi kerak. So'ng admin yarating (pastdagi "Mijoz qo'shish" bo'limi) va `https://pbx.atcpbx.uz/login` orqali kiring.
OnlinePBX'da webhook manzili: kabinetdagi `https://pbx.atcpbx.uz/hook/<token>`.

## Boshqaruv
- Yangilash: `cd /opt/pbxbot && git pull && venv/bin/pip install -r saas/requirements.txt && systemctl restart pbxbot`
- Loglar: `journalctl -u pbxbot -f`
- Zaxira: `sudo -u postgres pg_dump pbxbot > backup.sql` (cron bilan)
- Jadvallar birinchi ishga tushishda avtomatik yaratiladi (Alembic hali yo'q).

Docker variant ham bor: `docker-compose.yml` (bu hujjatning o'rniga).

## Mijozlar (tenantlar) va webhook
Har mijoz akkaunti (admin yaratadi) va noyob webhook manzilini oladi: `https://pbx.atcpbx.uz/hook/<uning-tokeni>`.
Mijoz uni kabinetdan nusxalab OnlinePBX'ga kiritadi; manzil oshkor bo'lsa kabinetda qayta yaratadi.

## Mijoz qo'shish
Saytda ro'yxatdan o'tish yo'q. Birinchi adminni serverda yarating:
```bash
cd /opt/pbxbot/saas && set -a && . ./.env && set +a && /opt/pbxbot/venv/bin/python -m app.create_user siz@kompaniya.uz PAROL --admin
```
(shu buyruq mavjud foydalanuvchining parolini ham almashtiradi). Keyin mijozlarni `/admin` → "Mijoz qo'shish" orqali qo'shasiz: email va parol qo'yib, ma'lumotni mijozga berasiz.

Yangilash: `cd /opt/pbxbot && git pull && venv/bin/pip install -r saas/requirements.txt && systemctl restart pbxbot`

## OnlinePBX API limitlaridan himoya
OnlinePBX'ning rasmiy limitlari bizga noma'lum, shuning uchun kod ehtiyotkor ishlaydi va hammasi `.env` orqali sozlanadi:
- Auth kaliti keshlanadi (`PBX_KEY_TTL`): har qo'ng'iroqda qayta `auth` qilinmaydi, 401/403 da bir marta yangilanadi.
- Tezlik: har mijozga `PBX_RPS_PER_TENANT` (2/s), umumiy `PBX_RPS_GLOBAL` (10/s) so'rov. Qo'ng'iroqlar ko'p kelsa, ular navbatda kutadi, yo'qolmaydi.
- 429/5xx: `Retry-After` yoki eksponensial kutish, ko'pi bilan `PBX_MAX_RETRIES` urinish, keyin kabinetda `error`.
- Noto'g'ri domen/kalit: `PBX_AUTH_COOLDOWN` soniya auth'ga urilmaydi (kalit o'zgartirilsa darrov tugaydi). Tarmoq xatosida tanaffus yo'q.
- Bir mijoz uchun kuniga `MAX_CALLS_PER_DAY` dan ortiq qo'ng'iroq qayta ishlanmaydi (`skipped`).
- Bir xil qo'ng'iroq (uuid) ikkinchi marta so'ralmaydi.
OnlinePBX qo'llab-quvvatlashidan haqiqiy limitni so'rab, shu qiymatlarni moslang. Kesh jarayon xotirasida: ilovani **bitta** uvicorn worker bilan ishlating (hozirgi systemd sozlamasi shunday).

## Kartaga o'tkazma orqali to'lov
Mijoz `/billing` sahifasida karta raqamini ko'radi, pulni o'tkazib chekni (JPG/PNG/WEBP/PDF) yuklaydi. Sizga Telegramda chek rasmi va **✅ Tasdiqlash / ❌ Rad etish** tugmalari bilan eslatma keladi; mijozga natija haqida uning chatiga xabar boradi. Saytdagi `/admin` ham ishlaydi.

Sozlash (`.env`):
- `PAYMENT_CARD` — karta raqami, `PAYMENT_CARD_HOLDER` — karta egasi.
- `ADMIN_CHAT_ID` — sizning **shaxsiy** chat ID'ingiz: botga `/id` yozing, bot raqamni qaytaradi. Avval botga `/start` bosib qo'ying, aks holda bot sizga yoza olmaydi. Faqat shu chatdagi tugmalar ishlaydi (guruh emas, shaxsiy chat tavsiya etiladi).
- Cheklar `saas/uploads/` papkasiga tushadi (zaxiraga qo'shing); ular faqat egasi va adminga ko'rinadi.

Yangilashda bir marta:
```bash
sed -i 's/client_max_body_size 5m/client_max_body_size 12m/' /etc/nginx/sites-available/pbxbot && nginx -t && systemctl reload nginx
cd /opt/pbxbot/saas && set -a && . ./.env && set +a && /opt/pbxbot/venv/bin/python -m app.setup_webhook   # tugmalar uchun callback_query yoqiladi
```
Baza avtomatik yangilanadi (`payments` jadvaliga ustunlar qo'shiladi).
