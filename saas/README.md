# PBX → Telegram (SaaS)

FastAPI + PostgreSQL. Har mijoz o'z OnlinePBX stansiyasini ulaydi, qo'ng'iroq yozuvlari uning Telegram chatlariga (har chat o'z tilida: uz/ru) yuboriladi. Bitta umumiy bot.

## Oqim
1. Mijoz ro'yxatdan o'tadi (sinov muddati `TRIAL_DAYS`) → kabinetda OnlinePBX domeni va API kalitini kiritadi (kalit Fernet bilan shifrlanadi).
2. Kabinetdagi webhook manzili (`/hook/<token>`) OnlinePBX'ga kiritiladi.
3. Chat ulash: kabinetdagi `t.me/<bot>?start=<kod>` havolasi (guruhda `/start@bot <kod>`) yoki Chat ID'ni qo'lda kiritish. Til faqat kabinetda tanlanadi (Telegramda til tugmalari yo'q).
4. Webhook kelganda: yozuv OnlinePBX API'dan olinadi va akkauntning barcha chatlariga shablon bo'yicha yuboriladi (takror uuid e'tiborsiz).
5. Obuna tugasa yuborish to'xtaydi; tarif `config.py` dagi `PLANS`.

## Ishga tushirish
```
pip install -r requirements.txt
cp .env.example .env   # to'ldiring, so'ng: set -a; . ./.env; set +a
python -c "from app import telegram; print(telegram.set_webhook())"   # Telegram webhook
uvicorn app.main:app
pytest
```
`ADMIN_EMAIL` bilan ro'yxatdan o'tgan foydalanuvchi admin bo'ladi (`/admin`).

## Hali qilinmagan / tekshirilmagan
- **To'lov**: Click/Payme integratsiyasi yo'q. Hozir mijoz so'rov yuboradi, admin `/admin` da qo'lda tasdiqlaydi (`service.confirm_payment` tayyor — provayder callback'i shuni chaqirishi kerak).
- **OnlinePBX webhook formati va API** (`pbx.py`) real stansiyada sinalmagan; webhook qaysi hodisada (qo'ng'iroq tugaganda, yozuv tayyor bo'lgach) kelishini tekshiring.
- Alembic migratsiyalari, parolni tiklash, email tasdiqlash, rate-limit, CSRF token yo'q (hozircha faqat SameSite=Lax cookie).
