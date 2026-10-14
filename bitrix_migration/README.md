# Bitrix24: bitrix24.ru -> bitrix24.kz (1-bosqich: sozlamalar)

1. Ikkala portalda: Dasturchi resurslari -> Boshqa -> Kiruvchi webhook (huquqlar: crm, department, user).
2. `cp .env.example .env` va webhook URL'larni kiriting (`.env` gitga kirmaydi).
3. `pip install requests`
4. `python settings_migrate.py export`
5. `python settings_migrate.py import` (dry-run), tekshirib, so'ng `--apply`.

Ko'chadi: bo'limlar, CRM spravochniklari, bitim voronkalari/bosqichlari, maxsus maydonlar.
Ko'chmaydi (qo'lda): foydalanuvchilar (export/settings.json da ro'yxat bor), rollar/huquqlar,
avtomatizatsiya, biznes-jarayonlar, telefoniya, integratsiyalar, tarif.
