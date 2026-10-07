# PBX → Telegram

OnlinePBX qo'ng'iroq yozuvlarini mijozning Telegram chatlariga (shaxsiy chat, guruh, kanal) tayyor shablon bilan, **o'zbek yoki rus tilida** yuboruvchi ko'p mijozli (SaaS) xizmat. Pro tarifda hisobotlar, qidiruv va ogohlantirishlar bor.

```
 OnlinePBX ──webhook──▶  FastAPI (uvicorn)  ──▶  Telegram Bot API ──▶ mijoz chatlari
 (har mijozning            │      │   │
  o'z domeni, kaliti)      │      │   └─ fon vazifalari: kunlik hisobot (scheduler), eski tarixni yuklash
                           │      └──── PostgreSQL: mijozlar, chatlar, qo'ng'iroqlar, to'lovlar
                           └─ brauzer: kabinet, hisobotlar, qidiruv, admin (SSE bilan jonli yangilanish)
```

## Imkoniyatlar

| | Sinov (7 kun) | Start | Pro |
|---|:-:|:-:|:-:|
| Qo'ng'iroq yozuvi Telegramga (uz/ru shablon, har chat o'z tilida) | ✓ | ✓ | ✓ |
| Chatlar soni | 3 | 3 | 20 |
| Hisobotlar (kunlar, soatlar, xodimlar), CSV eksport, eski tarixni yuklash | | | ✓ |
| Kunlik hisobot Telegramga | | | ✓ |
| Javobsiz qo'ng'iroq ogohlantirishi | | | ✓ |
| Qo'ng'iroqlarni qidirish, yozuvni tinglash/qayta yuborish | | | ✓ |
| Jonli yangilanish (sahifa o'zi yangilanadi) | | | ✓ |

Tarif narxlari va limitlari: [`app/config.py`](app/config.py) (`PLANS`). Mijozlarni administrator yaratadi (ommaviy ro'yxatdan o'tish yo'q), to'lov kartaga o'tkazma + chek orqali, administrator Telegramdan bir tugma bilan tasdiqlaydi.

## Tuzilma

```
app/
  main.py          ASGI ilova: middleware, marshrutlar, /healthz
  config.py        sozlamalar (muhit o'zgaruvchilari) va tarif jadvali
  models.py        SQLAlchemy modellari (User, Account, Chat, CallLog, Payment)
  db.py            ulanish va yengil migratsiya (init_db)
  hooks.py         OnlinePBX va Telegram webhook'lari
  service.py       asosiy mantiq: qo'ng'iroqni qayta ishlash, to'lovlar, tarixni yuklash
  pbx.py           OnlinePBX API mijozi (kesh, tezlik chegarasi, qayta urinish)
  telegram.py      Telegram Bot API
  reports.py       statistika, grafiklar (SVG), CSV, kunlik hisobot matni
  scheduler.py     fon: kunlik hisobot
  live.py          jonli yangilanish (SSE) hodisalar markazi
  middleware.py    xavfsizlik sarlavhalari, CSRF (Origin) tekshiruvi, texnik ishlar rejimi
  restore.py       bazani zaxiradan tiklash (tekshiruv, nusxa, bitta tranzaksiyada almashtirish)
  ratelimit.py     kirishdagi brute-force himoyasi
  security.py      parol xeshi, shifrlash;  receipts.py — cheklarni xavfsiz saqlash
  web/             sahifalar: auth, cabinet, billing, analytics, calls, stream, admin
  templates/       Jinja2 shablonlari (tashqi kutubxonasiz, qorong'i/yorug' rejim)
deploy/            install.sh, update.sh, backup.sh (+ app/backup_cli.py), systemd, nginx, cloudflare-realip.sh
tests/             pytest (mavzu bo'yicha modullar; conftest.py — umumiy fixturalar)
```

## Lokal ishlab chiqish

```bash
make install     # .venv va bog'liqliklar
make test        # testlar
make check       # lint + format + testlar + xavfsizlik (CI shuni bajaradi)
make run         # http://localhost:8000  (SQLite, scheduler o'chiq)
python -m app.create_user siz@mail.uz PAROL --admin   # birinchi admin
```

`make fmt` kodni avtomatik tuzatadi va formatlaydi. CI: [`.github/workflows/saas-ci.yml`](../.github/workflows/saas-ci.yml).

## Ishga tushirish va ishlatish

To'liq qo'llanma: **[DEPLOY.md](DEPLOY.md)** (o'rnatish, yangilash, zaxira, diagnostika, muammolar). Qisqacha:

```bash
bash /opt/pbxbot/saas/deploy/update.sh      # yangilash (kod, bog'liqliklar, restart, sog'liq tekshiruvi)
bash /opt/pbxbot/saas/deploy/backup.sh      # zaxira (har kuni 23:59 o'zi ishlaydi: shifrlab adminning Telegramiga)
curl http://127.0.0.1:8000/healthz          # holat: baza, scheduler, versiya
journalctl -u pbxbot -f                     # loglar
```

## Xavfsizlik

- Parollar `scrypt` bilan xeshlanadi; OnlinePBX API kalitlari `SECRET_KEY` bilan shifrlangan holda saqlanadi.
- Sessiya cookie: `HttpOnly`, `SameSite=Lax`, HTTPS'da `Secure`; CSRF — Origin/Referer tekshiruvi (webhook'lardan tashqari).
- Kirishda brute-force himoyasi (email va IP bo'yicha), foydalanuvchi mavjudligi xabarda oshkor bo'lmaydi.
- Xavfsizlik sarlavhalari (CSP, `X-Frame-Options`, HSTS, ...); API hujjat sahifalari o'chirilgan.
- Har mijoz faqat o'z ma'lumotini ko'radi; cheklar faqat egasi va administratorga (fayl turi mazmuniga qarab tekshiriladi).
- OnlinePBX API limitlaridan himoya: kalit keshi, tezlik chegarasi, 429 da kutish, noto'g'ri kalit uchun tanaffus.
- Bazani panel orqali tiklash standart holatda o'chiq (`ALLOW_PANEL_RESTORE=1`); zaxira avval tekshiriladi, admin paroli va tasdiqlash so'zi so'raladi, joriy baza nusxalanadi, almashtirish bitta tranzaksiyada.
- Yozuv fayllari serverda saqlanmaydi (OnlinePBX'dan oqim sifatida uzatiladi).

## Cheklovlar va keyingi qadamlar

- Ilova **bitta jarayon**da ishlaydi (jonli yangilanish va scheduler xotirada). Ko'p worker uchun hodisalar Redis orqali uzatilishi kerak.
- Migratsiyalar yengil (`db.init_db`: yangi ustunlarni qo'shadi). Ustun o'chirish/o'zgartirish kerak bo'lsa — Alembic'ga o'tish.
- To'lov hozircha kartaga o'tkazma + qo'lda tasdiqlash; Click/Payme avtomatik integratsiyasi yo'q.
- OnlinePBX API javob formati real stansiyada tekshirilgan (qo'ng'iroq ma'lumoti, yozuv, tarix); rasmiy limitlar noma'lum, shuning uchun hammasi `.env` orqali sozlanadi.
