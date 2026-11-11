"""CSV/XLSX dan Bitrix24 ga lid import qiluvchi skript.

Ishlatish:
    python import_leads.py leads.csv --dry-run     # faqat tekshirish
    python import_leads.py leads.xlsx              # haqiqiy import
    python import_leads.py leads.csv --source-id WEB --skip-duplicates

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
BATCH = 50  # Bitrix batch chegarasi


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


def build_lead(row, cmap, source_id):
    f = {}
    for col, key in cmap.items():
        v = (row.get(col) or "").strip()
        if v:
            f[key] = v
    if "PHONE" in f:
        ph = clean_phone(f["PHONE"])
        f["PHONE"] = [{"VALUE": ph, "VALUE_TYPE": "WORK"}] if ph else []
    if "EMAIL" in f:
        f["EMAIL"] = [{"VALUE": f["EMAIL"], "VALUE_TYPE": "WORK"}]
    if source_id and "SOURCE_ID" not in f:
        f["SOURCE_ID"] = source_id
    f.setdefault("TITLE", " ".join(filter(None, [f.get("NAME"), f.get("LAST_NAME")])) or "Import lid")
    return f


def call(base, method, payload):
    for attempt in range(5):
        r = requests.post(base + method, json=payload, timeout=60)
        if r.status_code in (429, 503):
            time.sleep(2 ** attempt)
            continue
        r.raise_for_status()
        data = r.json()
        if "error" in data:
            raise RuntimeError(f"{data['error']}: {data.get('error_description')}")
        return data
    raise RuntimeError("Bitrix band (rate limit)")


def existing_phone(base, phone):
    res = call(base, "crm.duplicate.findbycomm", {"type": "PHONE", "values": [phone]})["result"]
    return bool(res and res.get("LEAD"))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("file")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--source-id", default="")
    ap.add_argument("--skip-duplicates", action="store_true", help="telefon bo'yicha mavjud lidni o'tkazib yuborish")
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
    print("Taniligan ustunlar:", {k: v for k, v in cmap.items()})
    leads = [build_lead(r, cmap, a.source_id) for r in rows]

    if a.skip_duplicates and not a.dry_run:
        kept = []
        for l in leads:
            ph = l.get("PHONE") and l["PHONE"][0]["VALUE"]
            if ph and existing_phone(base, ph):
                continue
            kept.append(l)
        print(f"Dublikat o'tkazildi: {len(leads) - len(kept)}")
        leads = kept

    if a.dry_run:
        print(f"{len(leads)} ta lid. Birinchisi:\n{leads[0]}")
        return

    ok = 0
    for i in range(0, len(leads), BATCH):
        chunk = leads[i:i + BATCH]
        cmd = {str(n): "crm.lead.add?" + requests.compat.urlencode(flatten({"fields": l})) for n, l in enumerate(chunk)}
        res = call(base, "batch", {"halt": 0, "cmd": cmd})["result"]
        ok += len(res.get("result", {}))
        for k, e in (res.get("result_error") or {}).items():
            print(f"Xato (qator {i + int(k) + 2}): {e}")
    print(f"Yuklandi: {ok}/{len(leads)}")


def flatten(d, prefix=""):
    out = {}
    if isinstance(d, dict):
        for k, v in d.items():
            out.update(flatten(v, f"{prefix}[{k}]" if prefix else k))
    elif isinstance(d, list):
        for i, v in enumerate(d):
            out.update(flatten(v, f"{prefix}[{i}]"))
    else:
        out[prefix] = d
    return out


if __name__ == "__main__":
    main()
