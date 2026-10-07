"""Pro tarif hisobotlari: saqlangan qo'ng'iroqlar (CallLog) bo'yicha statistika, SVG grafiklar, CSV."""
import csv
import io
from collections import Counter, defaultdict
from datetime import date, datetime, time, timedelta, timezone
from html import escape

from sqlalchemy.orm import Session

from .messages import LOCAL_TZ, fmt_duration
from .models import CallLog

PRESETS = {"today": 1, "7d": 7, "30d": 30, "90d": 90}
MAX_DAYS = 366
MAX_CSV_ROWS = 50_000


def today_local() -> date:
    return datetime.now(LOCAL_TZ).date()


def parse_period(period: str | None, frm: str | None, to: str | None) -> tuple[date, date, str]:
    """(boshlanish, tugash, belgi). Noto'g'ri kiritilsa 7 kunga qaytadi. Sanalar mahalliy (UTC+5)."""
    today = today_local()
    if frm and to:
        try:
            a, b = date.fromisoformat(frm), date.fromisoformat(to)
            if a <= b and (b - a).days < MAX_DAYS:
                return a, min(b, today), "custom"
        except ValueError:
            pass
    days = PRESETS.get(period or "7d", 7)
    return today - timedelta(days=days - 1), today, period if period in PRESETS else "7d"


def utc_bounds(start: date, end: date) -> tuple[datetime, datetime]:
    """Mahalliy [start, end] kunlarni UTC (naive) yarim ochiq oraliqqa aylantiradi."""
    def conv(d: date) -> datetime:
        return datetime.combine(d, time(0), LOCAL_TZ).astimezone(timezone.utc).replace(tzinfo=None)
    return conv(start), conv(end + timedelta(days=1))


def load(db: Session, account_id: int, start: date, end: date, limit: int | None = None) -> list[CallLog]:
    lo, hi = utc_bounds(start, end)
    q = (db.query(CallLog).filter(CallLog.account_id == account_id, CallLog.started_at >= lo, CallLog.started_at < hi)
         .order_by(CallLog.started_at.desc()))
    return q.limit(limit).all() if limit else q.all()


def local(dt: datetime) -> datetime:
    return dt.replace(tzinfo=timezone.utc).astimezone(LOCAL_TZ)


def is_internal(n: str) -> bool:
    return n.isdigit() and 1 <= len(n) <= 4


def parties(r: CallLog) -> tuple[str, str]:
    """(xodim ichki raqami, tashqi raqam) — yo'nalishga qarab; aniqlanmasa bo'sh qator."""
    a, b = r.caller or "", r.callee or ""
    if r.direction == "inbound":
        emp, ext = b, a
    elif r.direction == "outbound":
        emp, ext = a, b
    else:  # local yoki noma'lum: qaysi biri ichki raqam bo'lsa
        emp, ext = (a, b) if is_internal(a) else (b, a)
        if r.direction == "local":
            ext = ""
    return (emp if is_internal(emp) else ""), (ext if ext and not is_internal(ext) else "")


def talk_seconds(r: CallLog) -> int:
    return (r.talk if r.talk is not None else r.duration) or 0


def answered(r: CallLog) -> bool:
    return talk_seconds(r) > 0


def build(rows: list[CallLog], start: date, end: date) -> dict:
    total = len(rows)
    ans = [r for r in rows if answered(r)]
    talk_total = sum(talk_seconds(r) for r in ans)
    dirs = Counter(r.direction or "?" for r in rows)

    by_day = {start + timedelta(days=i): [0, 0] for i in range((end - start).days + 1)}  # [soni, suhbat soniyasi]
    by_hour = [0] * 24
    emp: dict[str, dict] = defaultdict(lambda: {"calls": 0, "in": 0, "out": 0, "missed": 0, "talk": 0})
    ext = Counter()
    missed_ext = Counter()
    for r in rows:
        dt = local(r.started_at)
        if dt.date() in by_day:
            by_day[dt.date()][0] += 1
            by_day[dt.date()][1] += talk_seconds(r) if answered(r) else 0
        by_hour[dt.hour] += 1
        e, x = parties(r)
        if e:
            d = emp[e]
            d["calls"] += 1
            d["in"] += r.direction == "inbound"
            d["out"] += r.direction == "outbound"
            d["missed"] += not answered(r)
            d["talk"] += talk_seconds(r) if answered(r) else 0
        if x:
            ext[x] += 1
            if not answered(r):
                missed_ext[x] += 1
    return {
        "total": total, "answered": len(ans), "missed": total - len(ans),
        "answer_rate": round(100 * len(ans) / total) if total else 0,
        "inbound": dirs.get("inbound", 0), "outbound": dirs.get("outbound", 0), "local": dirs.get("local", 0),
        "talk_total": fmt_duration(talk_total), "talk_avg": fmt_duration(talk_total // len(ans)) if ans else "00:00",
        "by_day": [(d, c, t) for d, (c, t) in by_day.items()], "by_hour": by_hour,
        "employees": sorted(({"ext": k, **v, "talk_fmt": fmt_duration(v["talk"])} for k, v in emp.items()),
                            key=lambda x: -x["calls"])[:15],
        "top_numbers": ext.most_common(10), "missed_numbers": missed_ext.most_common(5),
        "busiest_hour": max(range(24), key=lambda h: by_hour[h]) if total else None,
    }


# ---------- SVG grafik (tashqi kutubxonasiz; ranglar CSS o'zgaruvchilaridan) ----------
def bar_svg(items: list[tuple[str, int, str]], height: int = 150, cls: str = "chart", w: int = 860) -> str:
    """items: (belgi, qiymat, tooltip). Qiymat 0 bo'lsa ham o'rin saqlanadi."""
    n = len(items)
    if not n:
        return ""
    pad_b, pad_t = 22, 8
    peak = max(v for _, v, _ in items) or 1
    slot = w / n
    bw = max(2.0, slot * 0.7)
    label_every = max(1, n // 12)
    out = [f'<svg class="{cls}" viewBox="0 0 {w} {height}" role="img">']
    for i, (label, v, tip) in enumerate(items):
        h = (height - pad_b - pad_t) * v / peak
        x = i * slot + (slot - bw) / 2
        out.append(f'<rect x="{x:.1f}" y="{height - pad_b - h:.1f}" width="{bw:.1f}" height="{max(h, 1 if v else 0):.1f}" rx="2">'
                   f'<title>{escape(tip)}</title></rect>')
        if i % label_every == 0:
            out.append(f'<text x="{x + bw / 2:.1f}" y="{height - 6}" text-anchor="middle">{escape(label)}</text>')
    out.append("</svg>")
    return "".join(out)


def day_chart(by_day) -> str:
    items = [(d.strftime("%d.%m"), c, f"{d.strftime('%d.%m.%Y')}: {c} ta, suhbat {fmt_duration(t)}") for d, c, t in by_day]
    return bar_svg(items, w=860 if len(items) > 14 else 440)  # ustun kam bo'lsa ixchamroq: yozuvlar o'qiladigan bo'lib qoladi


def hour_chart(by_hour) -> str:
    return bar_svg([(f"{h:02d}", c, f"{h:02d}:00–{h:02d}:59: {c} ta") for h, c in enumerate(by_hour)], cls="chart hrs", w=440)


# ---------- CSV ----------
def _safe(v) -> str:
    """CSV/Excel formula in'ektsiyasidan himoya: =, @ yoki raqam bo'lmagan +/- bilan boshlansa ' qo'shiladi."""
    s = str(v)
    if s[:1] in ("=", "@") or (s[:1] in ("+", "-") and not s[1:].isdigit()):
        return "'" + s
    return s


def to_csv(rows: list[CallLog]) -> str:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["Sana", "Yo'nalish", "Kimdan", "Kimga", "Xodim", "Davomiylik (s)", "Suhbat (s)", "Holat", "ID"])
    for r in rows:
        e, _ = parties(r)
        w.writerow([local(r.started_at).strftime("%Y-%m-%d %H:%M:%S"), _safe(r.direction), _safe(r.caller), _safe(r.callee),
                    _safe(e), r.duration or 0, talk_seconds(r), "javob berilgan" if answered(r) else "javob berilmagan", r.uuid])
    return "﻿" + buf.getvalue()  # BOM: Excel UTF-8 ni to'g'ri ochadi


# ---------- Telegram uchun kunlik hisobot matni ----------
DIGEST = {
    "uz": {"title": "Kunlik hisobot", "test": "sinov", "summary": "Jami: <b>{total}</b> · javob berilgan: <b>{answered}</b> ({rate}%) · javobsiz: <b>{missed}</b>",
           "dirs": "Kiruvchi: {inbound} · chiquvchi: {outbound}", "talk": "Suhbat vaqti: {talk} (o'rtacha {avg})",
           "busy": "Eng gavjum soat: {hour}:00", "emps": "Xodimlar", "emp": "<code>{ext}</code> — {calls} ta, javobsiz {missed}",
           "missed": "Javobsiz raqamlar", "num": "<code>{num}</code> — {n} ta"},
    "ru": {"title": "Дневной отчёт", "test": "тест", "summary": "Всего: <b>{total}</b> · отвечено: <b>{answered}</b> ({rate}%) · пропущено: <b>{missed}</b>",
           "dirs": "Входящие: {inbound} · исходящие: {outbound}", "talk": "Время разговоров: {talk} (в среднем {avg})",
           "busy": "Самый загруженный час: {hour}:00", "emps": "Сотрудники", "emp": "<code>{ext}</code> — {calls}, пропущено {missed}",
           "missed": "Пропущенные номера", "num": "<code>{num}</code> — {n}"},
}


def render_digest(lang: str, day: date, d: dict, test: bool = False) -> str:
    t = DIGEST.get(lang) or DIGEST["uz"]
    title = f"📊 <b>{t['title']}</b> · {day.strftime('%d.%m.%Y')}" + (f" ({t['test']})" if test else "")
    lines = [title, "", t["summary"].format(rate=d["answer_rate"], **{k: d[k] for k in ("total", "answered", "missed")}), t["dirs"].format(inbound=d["inbound"], outbound=d["outbound"]), t["talk"].format(talk=d["talk_total"], avg=d["talk_avg"])]
    if d["busiest_hour"] is not None:
        lines.append(t["busy"].format(hour=f"{d['busiest_hour']:02d}"))
    if d["employees"]:
        lines += ["", f"<b>{t['emps']}</b>"] + [t["emp"].format(ext=escape(e["ext"]), calls=e["calls"], missed=e["missed"]) for e in d["employees"][:5]]
    if d["missed_numbers"]:
        lines += ["", f"<b>{t['missed']}</b>"] + [t["num"].format(num=escape(n), n=c) for n, c in d["missed_numbers"]]
    return "\n".join(lines)
