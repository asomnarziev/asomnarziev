# Ishga tushirish va ishlatish qo'llanmasi

Ubuntu/Debian VPS (Docker'siz). Domen: `pbx.atcpbx.uz`, ilova `/opt/pbxbot` da, `pbxbot` foydalanuvchisi nomidan ishlaydi.

## 1. Birinchi o'rnatish

**Talablar:** root huquqi, domenning A-yozuvi server IP'siga qaragan (`dig +short pbx.atcpbx.uz`; Birinchi o'rnatishda Cloudflare'da proxy **o'chiq**, "DNS only"; keyin 8-bo'limga qarang), 80/443 portlar ochiq, @BotFather'dan bot tokeni.

```bash
# 1) kod
apt-get update && apt-get install -y git
git clone https://github.com/asomnarziev/asomnarziev.git /opt/pbxbot
cd /opt/pbxbot && git checkout claude/festive-newton-wd3kc4      # PR main'ga qo'shilgach: main

# 2) sozlamalar (.env.example ichida har bir qiymat izohlangan)
cp saas/.env.example saas/.env
echo "DB parol:   $(openssl rand -hex 16)"; echo "SECRET_KEY: $(openssl rand -hex 32)"; echo "TG secret:  $(openssl rand -hex 16)"
nano saas/.env          # DATABASE_URL (parol), SECRET_KEY, TG_WEBHOOK_SECRET, BOT_TOKEN, BOT_USERNAME, PAYMENT_CARD...

# 3) paketlar, baza, venv, systemd xizmati, kunlik zaxira
bash saas/deploy/install.sh
curl http://127.0.0.1:8000/healthz        # {"status":"ok",...}

# 4) nginx + HTTPS
cp saas/deploy/nginx.conf /etc/nginx/sites-available/pbxbot
ln -sf /etc/nginx/sites-available/pbxbot /etc/nginx/sites-enabled/pbxbot && rm -f /etc/nginx/sites-enabled/default
nginx -t && systemctl reload nginx
certbot --nginx -d pbx.atcpbx.uz

# 5) Telegram webhook va birinchi admin
cd /opt/pbxbot/saas && set -a && . ./.env && set +a
/opt/pbxbot/venv/bin/python -m app.setup_webhook                              # {'ok': True, ...}
/opt/pbxbot/venv/bin/python -m app.create_user siz@kompaniya.uz PAROL --admin
```

Keyin `https://pbx.atcpbx.uz/login` ga kiring. `SECRET_KEY` ni yo'qotmang: OnlinePBX kalitlari shu bilan shifrlangan.

## 2. Yangilash

```bash
bash /opt/pbxbot/saas/deploy/update.sh            # joriy branch
bash /opt/pbxbot/saas/deploy/update.sh main       # boshqa branch
```

Skript: kodni oladi (faqat fast-forward) → bog'liqliklarni yangilaydi → systemd faylini yangilaydi (o'zgargan bo'lsa) → egalikni to'g'rilaydi → qayta ishga tushiradi → `/healthz` ni 20 soniya kutadi. Xato bo'lsa loglarni va **orqaga qaytarish buyrug'ini** chiqaradi:

```bash
cd /opt/pbxbot && git checkout <oldingi-commit> && systemctl restart pbxbot
```

Baza o'zgarishlari (yangi ustunlar) ilova ishga tushganda avtomatik qo'llanadi. Qayta ishga tushirish fon vazifalarini (tarixni yuklash) uzadi: yuklash paytida restart qilmang.

## 3. Zaxira va tiklash

**Har kuni 23:59 da** (Toshkent vaqti; cron `59 18 * * *` UTC da, `/etc/cron.d/pbxbot-backup`) avtomatik:

1. Baza `pg_dump` bilan olinadi va **to'liqligi tekshiriladi** (dump oxiri bor-yo'qligi). Yaroqsiz bo'lsa, hech narsa o'chirilmaydi.
2. Yuklangan cheklar arxivlanadi (`/var/backups/pbxbot/`).
3. **Eski nusxalar o'chiriladi**: serverda faqat eng yangisi qoladi (`BACKUP_KEEP=1`, xohlasangiz ko'paytiring).
4. Baza **shifrlanib adminning Telegramiga** (`ADMIN_CHAT_ID`) yuboriladi: hajmi, mijozlar/qo'ng'iroqlar/to'lovlar soni bilan. Yangi xabar yetib borgach, **kechagi xabar o'chiriladi**, chatda doim bitta (eng yangi) zaxira turadi. Yuborib bo'lmasa, kechagisi tegilmaydi.

**Bir martalik sozlash** (`.env` ga):
```bash
echo "BACKUP_PASSPHRASE=$(openssl rand -base64 24)" >> /opt/pbxbot/saas/.env && grep BACKUP_PASSPHRASE /opt/pbxbot/saas/.env
```
Chiqqan parolni **parol menejerga yoki boshqa xavfsiz joyga ham nusxalang**: server yo'qolsa, usiz Telegramdagi zaxirani ochib bo'lmaydi. Parolsiz zaxira Telegramga yuborilmaydi (adminga ogohlantirish keladi), chunki unda mijozlar emaillari va qo'ng'iroq raqamlari bor. Telegram fayl chegarasi 50 MB: baza undan oshsa, ogohlantirish keladi.

Jadval `update.sh` bilan avtomatik yangilanadi. Server vaqt mintaqasi UTC ekanini tekshiring: `timedatectl | grep "Time zone"` (boshqacha bo'lsa, `deploy/pbxbot-backup.cron` dagi soatni moslang). Sinash: `bash /opt/pbxbot/saas/deploy/backup.sh` (Telegramga darrov keladi). Log: `/var/log/pbxbot-backup.log`.

```bash
# tiklash: Telegramdagi .enc faylni serverga ko'chiring, so'ng:
cd /opt/pbxbot/saas && /opt/pbxbot/venv/bin/python -m app.backup_cli decrypt /tmp/pbx_db_XXXX.sql.gz.enc /tmp/db.sql.gz   # parol so'raladi
systemctl stop pbxbot
sudo -u postgres dropdb pbxbot && sudo -u postgres createdb -O pbxbot pbxbot
gunzip -c /tmp/db.sql.gz | sudo -u postgres psql pbxbot
systemctl start pbxbot
# cheklar (serverdagi nusxadan): tar -xzf /var/backups/pbxbot/uploads_*.tar.gz -C /opt/pbxbot/saas && chown -R pbxbot:pbxbot /opt/pbxbot
```

Cheklar (`uploads`) Telegramga yuborilmaydi (faqat serverda). Bitta nusxa saqlash arzon va sodda, lekin "kecha o'chirib yuborilgan narsani bugun payqash" holatini yopmaydi: shunday xavf bo'lsa `BACKUP_KEEP=7` qo'ying (Telegramda esa baribir bitta qoladi). `.env` (ayniqsa `SECRET_KEY`) ni ham alohida xavfsiz joyda saqlang.

## 4. Diagnostika

```bash
systemctl status pbxbot                          # xizmat holati
curl -s http://127.0.0.1:8000/healthz            # baza, scheduler, versiya (503 bo'lsa muammo bor)
journalctl -u pbxbot -f                          # jonli log
journalctl -u pbxbot -n 100 --no-pager | grep -iE "error|failed|webhook"
```

Foydali bazadagi so'rovlar:

```bash
sudo -u postgres psql pbxbot -c "select status, count(*) from call_logs group by 1;"
sudo -u postgres psql pbxbot -c "select created_at, status, error from call_logs where status='error' order by id desc limit 5;"
sudo -u postgres psql pbxbot -c "select u.email, a.plan, a.paid_until, a.import_note from accounts a join users u on u.id=a.user_id;"
```

Webhook loglari (`webhook uuid=... fields=[...]`) INFO darajada yoziladi; `LOG_LEVEL=DEBUG` ko'proq ma'lumot beradi.

| Alomat | Sabab va yechim |
|---|---|
| Yozuv chatga kelmayapti, kabinetda `error` | Jadvaldagi izohni o'qing (`call_logs.error`): noto'g'ri API kalit/domen (kabinetda qayta kiriting), Telegram'da bot chatga yoza olmaydi (chatni qayta ulang) |
| OnlinePBX webhook tarixida qizil belgi | Manzil eskirgan (kabinetda "qayta yaratilgan" bo'lsa, OnlinePBX'ga yangisini kiriting) yoki server `5xx` qaytaryapti: `journalctl` |
| "jonli" nuqta kulrang | nginx javobni ushlab turibdi: `deploy/nginx.conf` dagi `/live/stream` blokini qo'shing |
| Kirishda "Juda ko'p urinish" | 15 daqiqa kuting (xato parol 5 marta); tezroq: `systemctl restart pbxbot` |
| Cheklar yuklanmayapti ("413") | nginx: `client_max_body_size 12m;` |
| `/healthz` 503, `scheduler: dead` | `systemctl restart pbxbot`, `journalctl` da sabab |

## 5. OnlinePBX, Telegram, to'lov sozlamalari

**OnlinePBX** (mijoz o'zi): Sozlamalar → Integratsiya → API'dan kalit; Webhook'larda kabinetdagi manzilni qo'shing, hodisa **"Завершили"** (javobsiz qo'ng'iroq ham shu bilan keladi). Webhook "Тест" so'rovi `200` bilan javob olishi kerak.

**Telegram bot:** yangi bot (mavjud botlarning tokenini ishlatmang: bitta botga bitta webhook). `setup_webhook` `message`, `channel_post` va `callback_query` yangilanishlarini so'raydi. Botga `/id` yozsangiz chat ID'ni qaytaradi.

**Kartaga to'lov:** `.env` da `PAYMENT_CARD`, `PAYMENT_CARD_HOLDER` va `ADMIN_CHAT_ID` (sizning shaxsiy chat ID'ingiz, avval botga `/start` bosing). Mijoz chek (yoki izoh) yuboradi → sizga Telegramda ✅/❌ tugmali eslatma keladi. Cheklar `saas/uploads/` da (zaxiraga kiradi), faqat egasi va administratorga ko'rinadi.

## 6. Mijozlar va tariflar

Ommaviy ro'yxatdan o'tish yo'q: administrator `/admin` → "Mijoz qo'shish" (login va parolni o'zi belgilaydi). Obuna: `/admin` → "Boshqarish" (uzaytirish, tugatish, parol, to'xtatish) yoki mijozning to'lovi tasdiqlanganda avtomatik. Tarif narxlari va imkoniyatlari: `app/config.py` → `PLANS`; yangi pullik imkoniyat: `features` ga nom qo'shib, kodda `acc.can("nom")` bilan tekshiring.

**Pro imkoniyatlari:** hisobotlar va CSV; kunlik hisobot (har kuni 09:00 Toshkent vaqti, kabinetda o'zgartiriladi, qo'ng'iroq bo'lmagan kunda yuborilmaydi); javobsiz kiruvchi qo'ng'iroq ogohlantirishi (yozuv so'ralmaydi, API tejaladi); qidiruv va yozuvni tinglash (OnlinePBX'dan oqim, diskka yozilmaydi, soatiga 60 ta); jonli yangilanish (SSE); **"Oxirgi 30 kunni yuklash"** — eski qo'ng'iroqlar ma'lumoti OnlinePBX tarixidan (kunma-kun so'raladi, takror xavfsiz; yozuv fayllari saqlanmaydi).

## 7. OnlinePBX API limitlaridan himoya

Rasmiy limitlar noma'lum, shuning uchun ehtiyotkor standartlar (`.env` da sozlanadi): auth kaliti keshi (`PBX_KEY_TTL`), mijoz/umumiy tezlik (`PBX_RPS_PER_TENANT=2`, `PBX_RPS_GLOBAL=10`), 429/5xx da `Retry-After` yoki eksponensial kutish (`PBX_MAX_RETRIES`), noto'g'ri domen/kalit uchun tanaffus (`PBX_AUTH_COOLDOWN`), kuniga `MAX_CALLS_PER_DAY` qo'ng'iroq. Bir xil `uuid` ikki marta so'ralmaydi. OnlinePBX qo'llab-quvvatlashidan haqiqiy limitni so'rab, qiymatlarni moslang.

## 8. Muhim cheklovlar

- **Bitta uvicorn worker.** Jonli yangilanish, kunlik hisobot va tezlik chegaralari jarayon xotirasida. `systemd` xizmati shunday sozlangan (`--workers 1`).
- Nginx orqasida ishlaydi: `--proxy-headers` real IP'ni beradi (kirish himoyasi shunga tayanadi).
- nginx versiyasini yashiring (xavfsizlik skanerlari buni kamchilik deb ko'rsatadi):
  `grep -q '^\s*server_tokens off' /etc/nginx/nginx.conf || sed -i '/^http {/a\    server_tokens off;' /etc/nginx/nginx.conf && nginx -t && systemctl reload nginx`.
  Yangilanishlar: `apt-get update && apt-get install --only-upgrade nginx`.
- Xizmat `NoNewPrivileges`, `ProtectSystem=full`, `ProtectHome` va boshqa systemd cheklovlari bilan ishlaydi.

## 9. Cloudflare orqali ishlatish (server IP'sini yashirish)

Cloudflare proxy (to'q sariq bulut) yoqilsa, tashrif buyuruvchilar va xavfsizlik skanerlari sizning IP'ingiz va joylashuvingiz o'rniga Cloudflare'ni ko'radi; qo'shimcha DDoS himoyasi ham olinadi. **Tartib muhim:**

```bash
# 1) AVVAL serverda haqiqiy foydalanuvchi IP'sini tiklang (aks holda kirish himoyasi hammani bitta IP deb ko'rib, bloklab qo'yadi)
bash /opt/pbxbot/saas/deploy/cloudflare-realip.sh
```

2. Cloudflare → **SSL/TLS → Overview: "Full (strict)"** (serverda Let's Encrypt sertifikati bor; "Flexible" qo'ysangiz cheksiz yo'naltirish xatosi chiqadi).
3. Cloudflare → **DNS** → `pbx` yozuvini oching → Proxy status: **Proxied** (to'q sariq).
4. Cloudflare → **Security**: *Bot Fight Mode* va *Under Attack* rejimini **o'chiring**, yoki WAF'da `/hook/*` va `/tg/*` yo'llari uchun "Skip" qoidasi qo'shing. Aks holda OnlinePBX va Telegram webhook'lari (brauzer emas, server so'rovlari) bloklanishi mumkin.
5. Tekshiring: kirish ishlayaptimi; "jonli" nuqta yashilmi; OnlinePBX'da webhook "Тест" `200` qaytaradimi; botga `/id` yozing; `journalctl -u pbxbot | grep "POST /login"` da haqiqiy IP ko'rinadimi (Cloudflare IP'si emas).

Cloudflare IP diapazonlari o'zgarsa skriptni qayta ishga tushiring (kamdan-kam). Sertifikat yangilanishi (certbot) proxy ortida ham ishlaydi; ishlamasa vaqtincha "DNS only" qiling.

**Halol cheklov:** bu IP'ni to'liq "yo'q" qilmaydi. Eski DNS yozuvlari tarixi (siz IP'ni bir necha marta o'zgartirgansiz), boshqa subdomenlar yoki serverga to'g'ridan-to'g'ri ulanish orqali asl IP topilishi mumkin. To'liq yashirish uchun serverda 80/443 portlarini faqat Cloudflare diapazonlariga oching va (ideal holda) yangi IP oling. Registrator/ro'yxatga olish ma'lumotlari (WHOIS) esa IP'ga bog'liq emas: ularni faqat domen registratoringizdagi "WHOIS privacy" sozlamasi yashiradi.

