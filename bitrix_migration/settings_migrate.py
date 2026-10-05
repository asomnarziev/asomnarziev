#!/usr/bin/env python3
"""Bitrix24 sozlamalarini bitrix24.ru dan bitrix24.kz ga ko'chirish (1-bosqich).

Ko'chiriladi: bo'limlar, CRM spravochniklari (statuslar), bitim voronkalari va
bosqichlari, CRM maxsus maydonlari (lead/deal/contact/company).
Foydalanuvchilar faqat JSON ga eksport qilinadi (import qilinmaydi).
Ma'lumotlar (lid, bitim, ...) bu skriptda YO'Q.

  python settings_migrate.py export            # .ru -> export/settings.json
  python settings_migrate.py import            # .kz ga quruq yurgizish (dry-run)
  python settings_migrate.py import --apply    # haqiqatda yozish
"""
import argparse, json, os, sys, time
from pathlib import Path
import requests

EXPORT = Path(__file__).parent / "export" / "settings.json"
ENTITIES = ["lead", "deal", "contact", "company"]
DELAY = 0.55  # Bitrix24 limiti ~2 so'rov/sek


def load_env():
    f = Path(__file__).parent / ".env"
    if f.exists():
        for line in f.read_text().splitlines():
            if "=" in line and not line.strip().startswith("#"):
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())


def call(base, method, params=None):
    time.sleep(DELAY)
    r = requests.post(base.rstrip("/") + "/" + method + ".json", json=params or {}, timeout=60)
    data = r.json()
    if "error" in data:
        raise RuntimeError(f"{method}: {data['error']} {data.get('error_description', '')}")
    return data


def call_all(base, method, params=None):
    out, start = [], 0
    while True:
        d = call(base, method, {**(params or {}), "start": start})
        res = d["result"]
        if isinstance(res, dict) and "categories" in res:
            res = res["categories"]
        out += res
        if "next" not in d:
            return out
        start = d["next"]


# ---------------------------------------------------------------- export
def do_export(src):
    data = {
        "departments": call_all(src, "department.get"),
        "users": call_all(src, "user.get", {"FILTER": {"USER_TYPE": "employee"}}),
        "statuses": call_all(src, "crm.status.list"),
        "categories": call_all(src, "crm.category.list", {"entityTypeId": 2}),
        "userfields": {e: call_all(src, f"crm.{e}.userfield.list") for e in ENTITIES},
    }
    EXPORT.parent.mkdir(exist_ok=True)
    EXPORT.write_text(json.dumps(data, ensure_ascii=False, indent=1))
    print("Eksport:", {k: (len(v) if not isinstance(v, dict) else {a: len(b) for a, b in v.items()}) for k, v in data.items()})
    print("Saqlandi:", EXPORT)


# ---------------------------------------------------------------- import
class Importer:
    def __init__(self, dst, apply):
        self.dst, self.apply = dst, apply

    def add(self, method, params, label):
        print(("  + " if self.apply else "  (dry) ") + label)
        if not self.apply:
            return None
        return call(self.dst, method, params)["result"]

    def departments(self, items):
        print("Bo'limlar")
        existing = {d["NAME"]: d["ID"] for d in call_all(self.dst, "department.get")}
        idmap = {}
        # ota-bo'lim birinchi yaratilishi uchun ID bo'yicha tartiblaymiz
        pending = sorted(items, key=lambda d: int(d["ID"]))
        for d in pending:
            if d["NAME"] in existing:
                idmap[d["ID"]] = existing[d["NAME"]]
                continue
            p = {"NAME": d["NAME"], "SORT": d.get("SORT", 500)}
            if d.get("PARENT"):
                parent = idmap.get(d["PARENT"])
                if parent:
                    p["PARENT"] = parent
            new = self.add("department.add", p, d["NAME"])
            idmap[d["ID"]] = new or "?"
            existing[d["NAME"]] = new

    def statuses(self, items):
        print("CRM spravochniklari (statuslar)")
        have = {(s["ENTITY_ID"], s["STATUS_ID"]) for s in call_all(self.dst, "crm.status.list")}
        for s in items:
            # voronka bosqichlari (DEAL_STAGE*) alohida qayta ishlanadi
            if s["ENTITY_ID"].startswith("DEAL_STAGE"):
                continue
            if (s["ENTITY_ID"], s["STATUS_ID"]) in have:
                continue
            self.add("crm.status.add", {"fields": {
                "ENTITY_ID": s["ENTITY_ID"], "STATUS_ID": s["STATUS_ID"],
                "NAME": s["NAME"], "SORT": s["SORT"]}}, f'{s["ENTITY_ID"]}: {s["NAME"]}')

    def pipelines(self, categories, statuses):
        print("Bitim voronkalari va bosqichlari")
        dst_cats = call_all(self.dst, "crm.category.list", {"entityTypeId": 2})
        by_name = {c["name"]: c["id"] for c in dst_cats}
        for c in sorted(categories, key=lambda c: int(c["id"])):
            sid = int(c["id"])
            if c["name"] in by_name:
                nid = int(by_name[c["name"]])
            else:
                res = self.add("crm.category.add", {"entityTypeId": 2, "fields": {
                    "name": c["name"], "sort": c.get("sort", 500)}}, f'voronka: {c["name"]}')
                nid = int(res["category"]["id"]) if res else None
            old_ent = "DEAL_STAGE" if sid == 0 else f"DEAL_STAGE_{sid}"
            new_ent = "DEAL_STAGE" if nid == 0 else f"DEAL_STAGE_{nid}"
            have = set()
            if nid is not None:
                have = {s["NAME"] for s in call_all(self.dst, "crm.status.list", {"filter": {"ENTITY_ID": new_ent}})}
            for s in (x for x in statuses if x["ENTITY_ID"] == old_ent):
                if s["NAME"] in have:
                    continue
                suffix = s["STATUS_ID"].split(":", 1)[-1]
                new_id = suffix if nid == 0 else f"C{nid}:{suffix}"
                self.add("crm.status.add", {"fields": {
                    "ENTITY_ID": new_ent, "STATUS_ID": new_id, "NAME": s["NAME"],
                    "SORT": s["SORT"], "COLOR": s.get("EXTRA", {}).get("COLOR", "")}},
                    f'  bosqich: {c["name"]} / {s["NAME"]}')

    def userfields(self, by_entity):
        print("CRM maxsus maydonlari")
        for e, items in by_entity.items():
            have = {u["FIELD_NAME"] for u in call_all(self.dst, f"crm.{e}.userfield.list")}
            for u in items:
                if u["FIELD_NAME"] in have:
                    continue
                f = {k: u[k] for k in ("USER_TYPE_ID", "FIELD_NAME", "XML_ID", "SORT", "MULTIPLE",
                                       "MANDATORY", "SHOW_FILTER", "SHOW_IN_LIST", "SETTINGS") if k in u}
                # FIELD_NAME "UF_CRM_..." — qo'shishda "UF_CRM_" prefiksisiz beriladi
                f["FIELD_NAME"] = u["FIELD_NAME"].replace("UF_CRM_", "", 1)
                for lbl in ("EDIT_FORM_LABEL", "LIST_COLUMN_LABEL", "LIST_FILTER_LABEL"):
                    if u.get(lbl):
                        f[lbl] = u[lbl]
                if u["USER_TYPE_ID"] == "enumeration" and u.get("LIST"):
                    f["LIST"] = [{"VALUE": i["VALUE"], "DEF": i.get("DEF", "N"), "SORT": i.get("SORT", 500)}
                                 for i in u["LIST"]]
                self.add(f"crm.{e}.userfield.add", {"fields": f}, f'{e}: {u["FIELD_NAME"]}')


def main():
    load_env()
    ap = argparse.ArgumentParser()
    ap.add_argument("mode", choices=["export", "import"])
    ap.add_argument("--apply", action="store_true", help="import: haqiqatda yozish (aks holda dry-run)")
    a = ap.parse_args()
    if a.mode == "export":
        src = os.environ.get("B24_RU_WEBHOOK") or sys.exit("B24_RU_WEBHOOK yo'q")
        do_export(src)
        return
    dst = os.environ.get("B24_KZ_WEBHOOK") or sys.exit("B24_KZ_WEBHOOK yo'q")
    d = json.loads(EXPORT.read_text())
    imp = Importer(dst, a.apply)
    if not a.apply:
        print("DRY-RUN: hech narsa yozilmaydi. Yozish uchun --apply.\n")
    imp.departments(d["departments"])
    imp.statuses(d["statuses"])
    imp.pipelines(d["categories"], d["statuses"])
    imp.userfields(d["userfields"])


if __name__ == "__main__":
    main()
