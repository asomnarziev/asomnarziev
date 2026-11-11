# Bitrix24 lid import

CSV/XLSX fayldan Bitrix24 CRM ga import: har qator uchun telefon (bo'lmasa email) bo'yicha kontakt qidiriladi — bor bo'lsa qayta yaratilmaydi, yo'q bo'lsa yaratiladi — keyin sdelka yaratilib kontaktga bog'lanadi.

1. Bitrix24: Developer resources → Boshqa → Kiruvchi webhook (CRM huquqi), URL ni oling.
2. `pip install -r requirements.txt`
3. `export BITRIX_WEBHOOK_URL=https://.../rest/1/xxxx/`
4. Tekshirish: `python import_leads.py leads.csv --dry-run`
5. Import: `python import_leads.py leads.csv --source-id WEB --category-id 0 --stage-id NEW --assigned-by 186`

Ustunlar: ism, familiya, telefon, email, kompaniya, izoh, manba, manzil, summa (uz/ru/en nomlar taniladi).
Telefon `+998...` ga keltiriladi (9 xonali bo'lsa 998 qo'shiladi).
