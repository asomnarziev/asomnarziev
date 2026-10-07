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
- `BASE_URL=https://pbx.atcpbx.uz`, `BOT_TOKEN`, `BOT_USERNAME`, `ADMIN_EMAIL`
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
`{"ok": true, ...}` chiqishi kerak. So'ng `https://pbx.atcpbx.uz/register` — `ADMIN_EMAIL` bilan ro'yxatdan o'tgan foydalanuvchi admin bo'ladi.
OnlinePBX'da webhook manzili: kabinetdagi `https://pbx.atcpbx.uz/hook/<token>`.

## Boshqaruv
- Yangilash: `cd /opt/pbxbot && git pull && venv/bin/pip install -r saas/requirements.txt && systemctl restart pbxbot`
- Loglar: `journalctl -u pbxbot -f`
- Zaxira: `sudo -u postgres pg_dump pbxbot > backup.sql` (cron bilan)
- Jadvallar birinchi ishga tushishda avtomatik yaratiladi (Alembic hali yo'q).

Docker variant ham bor: `docker-compose.yml` (bu hujjatning o'rniga).

## Mijozlar (tenantlar) va webhook
Har mijoz ro'yxatdan o'tganda o'z akkaunti va noyob webhook manzilini oladi: `https://pbx.atcpbx.uz/hook/<uning-tokeni>`.
Mijoz uni kabinetdan nusxalab OnlinePBX'ga kiritadi; manzil oshkor bo'lsa kabinetda qayta yaratadi.

## Mijoz qo'shish
`OPEN_REGISTRATION=0` (standart) bo'lsa `/register` yopiq: admin `/admin` → "Mijoz qo'shish" da email va parol qo'yib akkaunt yaratadi, ma'lumotni mijozga beradi.
Parolni `/admin` jadvalidan almashtirsa bo'ladi. Hamma ro'yxatdan o'tsin desangiz `.env` ga `OPEN_REGISTRATION=1` yozing.
Yangilash: `cd /opt/pbxbot && git pull && venv/bin/pip install -r saas/requirements.txt && systemctl restart pbxbot`
