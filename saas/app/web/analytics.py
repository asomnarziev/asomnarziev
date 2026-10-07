"""Pro: hisobotlar sahifasi, CSV eksport va eski qo'ng'iroqlarni OnlinePBX tarixidan yuklash."""

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from fastapi.responses import Response
from sqlalchemy.orm import Session

from .. import config, reports, service
from ..db import get_db
from ..models import User, now
from .deps import go, render, reports_allowed, require_user

router = APIRouter()


@router.get("/reports")
def reports_page(
    request: Request,
    period: str | None = None,
    frm: str | None = None,
    to: str | None = None,
    ok: str | None = None,
    error: str | None = None,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    if not reports_allowed(user):
        return render(request, "reports_locked.html", user=user, acc=user.account, plans=config.PLANS)
    start, end, mode = reports.parse_period(period, frm, to)
    data = reports.build(reports.load(db, user.account.id, start, end), start, end)
    return render(
        request,
        "reports.html",
        user=user,
        acc=user.account,
        r=data,
        start=start,
        end=end,
        mode=mode,
        ok=ok,
        error=error,
        importing=service.is_importing(user.account.id),
        import_days=service.IMPORT_DAYS,
        day_svg=reports.day_chart(data["by_day"]),
        hour_svg=reports.hour_chart(data["by_hour"]),
        qs=f"frm={start.isoformat()}&to={end.isoformat()}",
    )


@router.get("/reports.csv")
def reports_csv(
    period: str | None = None,
    frm: str | None = None,
    to: str | None = None,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    if not reports_allowed(user):
        raise HTTPException(403, "Hisobotlar Pro tarifda")
    start, end, _ = reports.parse_period(period, frm, to)
    body = reports.to_csv(reports.load(db, user.account.id, start, end, limit=reports.MAX_CSV_ROWS))
    return Response(
        body,
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="qongiroqlar_{start}_{end}.csv"',
            "Cache-Control": "private, no-store",
        },
    )


@router.post("/reports/import")
def reports_import(bg: BackgroundTasks, user: User = Depends(require_user)):
    """Oxirgi 30 kunlik eski qo'ng'iroqlarni OnlinePBX tarixidan hisobot/qidiruvga yuklaydi (fon vazifasi)."""
    if not reports_allowed(user):
        raise HTTPException(403, "Tarixni yuklash Pro tarifda")
    acc = user.account
    if not (acc.pbx_domain and acc.pbx_key_enc):
        return go("/reports", error="Avval kabinetda OnlinePBX domeni va API kalitini kiriting")
    if service.is_importing(acc.id):
        return go("/reports", error="Yuklash allaqachon davom etmoqda")
    if acc.import_at and (now() - acc.import_at).total_seconds() < service.IMPORT_COOLDOWN:
        return go("/reports", error="Yaqinda yuklangan. Biroz kutib qayta urinib ko'ring")
    bg.add_task(service.import_history, acc.id)
    return go("/reports", ok="import")
