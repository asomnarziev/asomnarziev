# Bitrix24 lid import

CSV/XLSX fayldan Bitrix24 CRM ga lidlarni batch (50 tadan) bilan yuklaydi.

1. Bitrix24: Developer resources → Boshqa → Kiruvchi webhook (CRM huquqi), URL ni oling.
2. `pip install -r requirements.txt`
3. `export BITRIX_WEBHOOK_URL=https://.../rest/1/xxxx/`
4. Tekshirish: `python import_leads.py leads.csv --dry-run`
5. Import: `python import_leads.py leads.csv --source-id WEB --skip-duplicates`

Ustunlar: ism, familiya, telefon, email, kompaniya, izoh, manba, manzil, summa (uz/ru/en nomlar taniladi).
Telefon `+998...` ga keltiriladi (9 xonali bo'lsa 998 qo'shiladi).
