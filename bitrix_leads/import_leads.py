"""CSV/XLSX dan Bitrix24 ga kontakt + sdelka import qiluvchi skript.

Har qator uchun: telefon bo'yicha kontakt qidiriladi; bo'lsa qayta yaratilmaydi,
yo'q bo'lsa yaratiladi; so'ng sdelka yaratilib, kontaktga bog'lanadi.

Ishlatish:
    python import_leads.py leads.csv --dry-run     # faqat tekshirish
    python import_leads.py leads.xlsx              # haqiqiy import
    python import_leads.py leads.csv --source-id WEB --category-id 0 --stage-id NEW --assigned-by 186

Ustun nomlari (uz/ru/en) avtomatik taniladi, qarang: COLUMN_ALIASES.
Webhook URL: BITRIX_WEBHOOK_URL muhit o'zgaruvchisida.
"""
import argparse
import csv
import os
import re
import sys
import time

import requests

COLUMN_ALIASES = {
    "TITLE": ["title", "sarlavha", "nomi", "название", "lid"],
    "NAME": ["name", "ism", "имя", "fio", "fish"],
    "LAST_NAME": ["last_name", "familiya", "фамилия"],
    "PHONE": ["phone", "telefon", "tel", "телефон", "raqam"],
    "EMAIL": ["email", "e-mail", "pochta", "почта"],
    "COMPANY_TITLE": ["company", "kompaniya", "компания", "tashkilot"],
    "COMMENTS": ["comment", "comments", "izoh", "комментарий", "note"],
    "SOURCE_ID": ["source", "manba", "источник"],
    "ADDRESS": ["address", "manzil", "адрес"],
    "OPPORTUNITY": ["amount", "summa", "сумма", "opportunity"],
}


def norm(s):
    return re.sub(r"\s+", "", str(s or "").strip().lower())


def read_rows(path):
    if path.lower().endswith((".xlsx", ".xlsm")):
        from openpyxl import load_workbook

        ws = load_workbook(path, read_only=True, data_only=True).active
        it = ws.iter_rows(values_only=True)
        header = [str(h or "") for h in next(it)]
        return [dict(zip(header, ["" if v is None else str(v) for v in r])) for r in it]
    with open(path, newline="", encoding="utf-8-sig") as f:
        sample = f.read(4096)
        f.seek(0)
        try:
            dialect = csv.Sniffer().sniff(sample, delimiters=",;\t")
        except csv.Error:
            dialect = csv.excel
        return list(csv.DictReader(f, dialect=dialect))


def map_columns(header):
    alias = {norm(a): k for k, v in COLUMN_ALIASES.items() for a in v + [k]}
    return {h: alias[norm(h)] for h in header if norm(h) in alias}


def clean_phone(p):
    digits = re.sub(r"\D", "", p or "")
    if not digits:
        return ""
    if len(digits) == 9:  # O'zbekiston mahalliy raqami
        digits = "998" + digits
    return "+" + digits


def split_row(row, cmap, source_id):
    f = {}
    for col, key in cmap.items():
        v = (row.get(col) or "").strip()
        if v:
            f[key] = v
    phone = clean_phone(f.get("PHONE", ""))
    contact = {k: f[k] for k in ("NAME", "LAST_NAME", "ADDRESS") if k in f}
    if phone:
        contact["PHONE"] = [{"VALUE": phone, "VALUE_TYPE": "WORK"}]
    if "EMAIL" in f:
        contact["EMAIL"] = [{"VALUE": f["EMAIL"], "VALUE_TYPE": "WORK"}]
    contact.setdefault("NAME", f.get("TITLE", "Import"))
    comments = f.get("COMMENTS", "")
    if f.get("COMPANY_TITLE"):
        comments = (comments + "\nKompaniya: " + f["COMPANY_TITLE"]).strip()
    deal = {"TITLE": f.get("TITLE") or " ".join(filter(None, [f.get("NAME"), f.get("LAST_NAME")])) or "Import sdelka"}
    if comments:
        deal["COMMENTS"] = comments
    if f.get("OPPORTUNITY"):
        deal["OPPORTUNITY"] = re.sub(r"[^\d.]", "", f["OPPORTUNITY"].replace(",", ".")) or 0
    if source_id or f.get("SOURCE_ID"):
        deal["SOURCE_ID"] = f.get("SOURCE_ID") or source_id
    return phone, contact, deal


def call(base, method, payload):
    for attempt in range(5):
        r = requests.post(base + method, json=payload, timeout=(10, 30))
        if r.status_code in (429, 503):
            time.sleep(2 ** attempt)
            continue
        r.raise_for_status()
        data = r.json()
        if "error" in data:
            raise RuntimeError(f"{data['error']}: {data.get('error_description')}")
        return data
    raise RuntimeError("Bitrix band (rate limit)")


def find_contact(base, phone, email):
    for typ, val in (("PHONE", phone), ("EMAIL", email)):
        if not val:
            continue
        res = call(base, "crm.duplicate.findbycomm", {"type": typ, "values": [val], "entity_type": "CONTACT"})["result"]
        if res and res.get("CONTACT"):
            return int(res["CONTACT"][0])
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("file")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--source-id", default="")
    ap.add_argument("--category-id", type=int, default=0, help="sdelka voronkasi ID (0 = asosiy)")
    ap.add_argument("--stage-id", default="NEW", help="bosqich (NEW = НОВЫЕ ЛИДЫ)")
    ap.add_argument("--assigned-by", type=int, default=186, help="mas'ul xodim ID")
    ap.add_argument("--only-new", action="store_true", help="kontakt Bitrixda bor bo'lsa qatorni o'tkazib yuborish (sdelka ham yaratilmaydi)")
    ap.add_argument("--limit", type=int, default=0, help="shuncha sdelka yaratilgach to'xtash (0 = hammasi)")
    ap.add_argument("--uz-only", action="store_true", help="faqat O'zbekiston raqamlari (+998 + 9 xona)")
    a = ap.parse_args()

    base = os.environ.get("BITRIX_WEBHOOK_URL", "")
    if not base.endswith("/"):
        base += "/"
    if not a.dry_run and base == "/":
        sys.exit("BITRIX_WEBHOOK_URL berilmagan")

    rows = read_rows(a.file)
    if not rows:
        sys.exit("Fayl bo'sh")
    cmap = map_columns(rows[0].keys())
    print("Taniligan ustunlar:", cmap)

    if a.dry_run:
        print(f"{len(rows)} ta qator. Birinchisi:")
        print(split_row(rows[0], cmap, a.source_id))
        return

    if a.uz_only:
        rows = [r for r in rows if re.fullmatch(r"\+998\d{9}", clean_phone(r.get(next((c for c, k in cmap.items() if k == "PHONE"), ""), "")))]
        print(f"O'zbek raqamli qatorlar: {len(rows)}")

    cache = {}  # telefon -> contact_id (fayl ichidagi takrorlar uchun)
    created = reused = deals = skipped = 0
    for n, row in enumerate(rows, start=2):
        if a.limit and deals >= a.limit:
            break
        try:
            phone, contact, deal = split_row(row, cmap, a.source_id)
            email = contact.get("EMAIL", [{}])[0].get("VALUE")
            cid = cache.get(phone) if phone else None
            if cid is None:
                cid = find_contact(base, phone, email)
                if cid and a.only_new:
                    skipped += 1
                    cache[phone] = cid
                    continue
                if cid:
                    reused += 1
                else:
                    contact["ASSIGNED_BY_ID"] = a.assigned_by
                    cid = int(call(base, "crm.contact.add", {"fields": contact})["result"])
                    created += 1
                if phone:
                    cache[phone] = cid
            elif a.only_new:
                skipped += 1
                continue
            else:
                reused += 1
            deal["CONTACT_ID"] = cid
            deal["CATEGORY_ID"] = a.category_id
            deal["STAGE_ID"] = a.stage_id
            deal["ASSIGNED_BY_ID"] = a.assigned_by
            call(base, "crm.deal.add", {"fields": deal})
            deals += 1
            if deals % 25 == 0:
                print(f"... {deals} sdelka", flush=True)
        except Exception as e:  # bitta qator xatosi qolganini to'xtatmasin
            print(f"Xato (qator {n}): {e}")
    print(f"Sdelka: {deals}/{len(rows)}; kontakt yangi: {created}, mavjud: {reused}, o'tkazildi (bor edi): {skipped}")


if __name__ == "__main__":
    main()
