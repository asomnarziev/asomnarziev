# Deploy (VPS, Docker)

Kerak: Ubuntu/Debian server, Docker + compose plugin, domen (A-yozuv server IP'siga), 80/443 portlar ochiq.

```bash
git clone https://github.com/asomnarziev/asomnarziev.git && cd asomnarziev/saas
cp .env.example .env && nano .env        # barcha maydonlarni to'ldiring
docker compose up -d --build
docker compose exec app python -m app.setup_webhook    # Telegram webhook -> {"ok": true,...}
```

Tekshirish: `https://<domen>/register` ochilishi, ro'yxatdan o'tgach `ADMIN_EMAIL` bilan admin bo'lishi.
OnlinePBX'da webhook manzili: kabinetdagi `https://<domen>/hook/<token>`.

Yangilash: `git pull && docker compose up -d --build`.
Zaxira: `docker compose exec db pg_dump -U pbxbot pbxbot > backup.sql` (cron bilan).
Loglar: `docker compose logs -f app`.

Eslatma: `SECRET_KEY` ni yo'qotmang — OnlinePBX kalitlari shu bilan shifrlangan.
Jadvallar birinchi ishga tushishda avtomatik yaratiladi (Alembic hali yo'q).
