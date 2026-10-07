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

## Hisobotlar (Pro tarif)
`/reports` sahifasi faqat **Pro** tarifdagi mijozlarga (va administratorga) ochiladi: kunlar va soatlar bo'yicha grafiklar, xodimlar (ichki raqamlar) reytingi, eng ko'p qo'ng'iroq raqamlari, javobsiz qo'ng'iroqlar va CSV (Excel) eksport. Boshqa tarifdagilar "Pro tarifga o'ting" sahifasini ko'radi.
Statistika webhook orqali kelgan qo'ng'iroqlardan to'planadi, shuning uchun yangilashdan **oldingi** qo'ng'iroqlar hisobotda bo'lmaydi. Bu OnlinePBX API limitiga tegmaslik uchun ham shunday: hisobot uchun API'ga qo'shimcha so'rov yuborilmaydi.
Pro imkoniyatlari `app/config.py` dagi `PLANS[...]["features"]` da belgilanadi (yangi pullik imkoniyatni shu yerga qo'shib, `acc.can("nom")` bilan tekshiriladi).
Javobsiz qo'ng'iroqlar to'liq ko'rinishi uchun OnlinePBX'da "Пропущенный" hodisasini ham webhook'ga yoqing (hozir faqat "Завершили").

## Kunlik hisobot (Pro)
Pro tarifdagi mijozlarga har kuni ertalab (standart 09:00, Toshkent vaqti; kabinetda o'zgartiriladi) kechagi hisobot ulangan chatlarga o'z tilida yuboriladi: jami/javob berilgan/javobsiz, kirish-chiqish, suhbat vaqti, eng gavjum soat, xodimlar va javobsiz raqamlar. Qo'ng'iroq bo'lmagan kunda xabar yuborilmaydi.
- Ilova ichidagi fon oqimi har daqiqada tekshiradi (alohida cron kerak emas). **Bitta uvicorn worker** bilan ishlating (hozirgi systemd sozlamasi shunday).
- Server o'chiq turib soat o'tib ketsa, yoqilgandan keyin o'sha kunning hisoboti yuboriladi; bir kunga ikki marta yuborilmaydi.
- Telegram xatosida 3 marta urinadi, keyin o'sha kunni o'tkazib yuboradi.
- O'chirish: `.env` ga `SCHEDULER=0`. Vaqt mintaqasi: `TZ_OFFSET_HOURS` (standart 5).

## Pro: javobsiz qo'ng'iroq ogohlantirishi va qidiruv
Ikkalasi ham faqat **Pro** tarifda (`features`: `missed_alerts`, `search`).
- **Javobsiz qo'ng'iroq:** kiruvchi qo'ng'iroqqa javob berilmasa, ulangan chatlarga darrov "📵 Javobsiz qo'ng'iroq" xabari (kim, kimga, qachon, necha soniya jiringlagan) o'z tilida keladi. Yozuv bo'lmagani uchun OnlinePBX API'dan yozuv so'ralmaydi. Kabinetdagi "Hisobot va ogohlantirishlar" kartasida o'chirib qo'yish mumkin. Chiquvchi javobsiz qo'ng'iroqlar bunga kirmaydi. **Webhook'da "Завершили" hodisasi yoqilgan bo'lishi kerak** (javobsiz qo'ng'iroq ham shu hodisa bilan keladi; kelmasa "Пропущенный" ni ham yoqing).
- **Qidiruv (`/calls`):** raqam (qisman), xodim, yo'nalish, holat va sana bo'yicha; sahifasiga 50 tadan. Yozuvni saytda tinglash/yuklab olish va Telegramga qayta yuborish mumkin. Yozuv har safar OnlinePBX'dan olinadi, shuning uchun soatiga `MAX_RECORD_FETCH_PER_HOUR` (standart 60) tadan ko'p emas.
- Qidiruv faqat yangilangandan keyin saqlangan qo'ng'iroqlarni topadi.

## Jonli yangilanish (Pro)
**Hisobotlar** va **Qo'ng'iroqlar** sahifalari ochiq tursa, yangi qo'ng'iroq kelishi bilan o'zi yangilanadi (sarlavhada yashil "jonli" nuqta). Yangi qatorlar bir necha soniya yorug' ko'rinadi. Foydalanuvchi pleyerni ochgan yoki yozuv eshitayotgan bo'lsa, sahifa o'zi almashmaydi: "N ta yangi qo'ng'iroq — Yangilash" paneli chiqadi.
- Texnik: Server-Sent Events (`/live/stream`), bazani band qilmaydi; bitta mijoz uchun 10 tagacha ochiq sahifa; 25 soniyada bir "ping".
- Faqat **bitta uvicorn worker** bilan ishlaydi (hodisalar jarayon xotirasida). Hozirgi systemd sozlamasi shunday.
- nginx: javobdagi `X-Accel-Buffering: no` sarlavhasi bufferlashni o'zi o'chiradi, qo'shimcha sozlash odatda kerak emas. Agar "jonli" nuqta yonmasa, `location /` ga `proxy_buffering off; proxy_http_version 1.1; proxy_read_timeout 1h;` qo'shing va `nginx -s reload` qiling.
- Ulanish uzilsa brauzer 3 soniyada o'zi qayta ulanadi va o'tkazib yuborilganini yangilaydi.

## Eski qo'ng'iroqlarni yuklash (Pro)
Hisobotlar sahifasida **"Oxirgi 30 kunni yuklash"** tugmasi: OnlinePBX tarixidan eski qo'ng'iroqlar **ma'lumoti** (kim, kimga, yo'nalish, vaqt, davomiylik) hisobot va qidiruvga qo'shiladi. **Yozuv fayllari serverga saqlanmaydi**: ularni Telegramdan eshitish yoki Qo'ng'iroqlar sahifasida "Tinglash" bosilganda OnlinePBX'dan oqim sifatida olinadi (diskka yozilmaydi).
- Kunma-kun so'raladi (API tezlik chegarasi bilan, 30 kun ~ 20-30 soniya); javob 500 qatordan oshsa oyna ikkiga bo'linadi (API qirqib tashlamasligi uchun). Bir yuklashda 20 000 qatorgacha.
- Takror yuklash xavfsiz: bor qo'ng'iroq ikki marta qo'shilmaydi. Muvaffaqiyatli yuklashdan keyin 10 daqiqa tanaffus.
- Yuklangan qatorlar "oxirgi webhook" ro'yxatiga va kunlik limitga ta'sir qilmaydi (`status = imported`).
- Birinchi marta yuklaganda natijani tekshiring: `journalctl -u pbxbot | grep history`. OnlinePBX tarix API'si kutilganidan boshqacha javob bersa, kabinetda sababi yoziladi.
- Xotira: har qo'ng'iroq ~200 bayt (kuniga 1000 ta qo'ng'iroq ~ yiliga 100 MB).
