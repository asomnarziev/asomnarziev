"""
admin_bot/main.py
-----------------
Ishga tushirish:
    cd ~/FreedomFlowers
    source venv/bin/activate
    python -m admin_bot.main
"""

import asyncio
import logging
import sys
from datetime import datetime, timedelta, timezone

from admin_bot.config import ADMIN_BOT_TOKEN, ADMIN_CHAT_IDS, MOYSKLAD_TOKEN
from admin_bot.db import init_db, get_doc, mark_sent, mark_deleted
from admin_bot.ms_client import (
    poll_demands,      deleted_demands,
    poll_paymentins,   deleted_paymentins,
    poll_salesreturns, deleted_salesreturns,
    poll_supplies,     deleted_supplies,
    get_counterparty_balance,
)
from admin_bot.notifier import send, delete_messages
from admin_bot.notifier import fmt_demand, fmt_paymentin, fmt_salesreturn, fmt_supply
from admin_bot.bot import run_bot
from admin_bot import state as bot_state

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("admin_bot")


def _check_config():
    errors = []
    if not ADMIN_BOT_TOKEN:
        errors.append("ADMIN_BOT_TOKEN .env da yo'q")
    if not ADMIN_CHAT_IDS:
        errors.append("ADMIN_CHAT_IDS .env da yo'q")
    if not MOYSKLAD_TOKEN:
        errors.append("MOYSKLAD_TOKEN .env da yo'q")
    if errors:
        for e in errors:
            logger.error(f"❌ {e}")
        sys.exit(1)
    logger.info(f"✅ Konfiguratsiya OK | Adminlar: {ADMIN_CHAT_IDS}")


# ── Entity konfiguratsiyasi ───────────────────────────────────────────────────

ENTITIES = {
    "demand":      (poll_demands,      deleted_demands,      fmt_demand),
    "paymentin":   (poll_paymentins,   deleted_paymentins,   fmt_paymentin),
    "salesreturn": (poll_salesreturns, deleted_salesreturns, fmt_salesreturn),
    "supply":      (poll_supplies,     deleted_supplies,     fmt_supply),
}

_poll_counter = 0


# ── Polling mantiq ────────────────────────────────────────────────────────────

def _process_entity(entity: str, poller, deleted_fn, formatter, since: datetime):

    # ── CREATE va UPDATE ──────────────────────────────────────────────────────
    items = poller(since)
    if items:
        logger.info(f"📨 {entity}: {len(items)} ta o'zgarish")

    for item in items:
        doc_id  = item.get("id", "")
        updated = item.get("updated", "")
        if not doc_id:
            continue

        existing = get_doc(entity, doc_id)

        if existing is None:
            # ── CREATE ──
            try:
                agent_id = item.get("agent_id", "")
                if agent_id:
                    item["balance"] = get_counterparty_balance(agent_id)
                text     = formatter(item, "CREATE")
                msg_data = send(text, entity)
                if not msg_data:
                    raise RuntimeError("xabar hech bir adminga yetib bormadi")
                doc_info = {k: v for k, v in item.items() if k not in ("positions", "id", "updated")}
                mark_sent(entity, doc_id, updated, msg_data, doc_info)
                logger.info(f"  ✅ CREATE {entity} {doc_id[:8]}...")
            except Exception as e:
                logger.error(f"  ❌ CREATE {entity} {doc_id[:8]}...: {e}")

        elif existing["updated_at"] != updated:
            # ── UPDATE — eski xabarni o'chirib yangi yuboramiz ──
            try:
                # agent_id ni item dan yoki eski doc_info dan olamiz
                agent_id = item.get("agent_id") or (existing.get("doc_info") or {}).get("agent_id", "")
                if agent_id:
                    item["balance"] = get_counterparty_balance(agent_id)

                # Eski xabarni o'chiramiz
                if existing["msg_data"]:
                    delete_messages(existing["msg_data"])

                # Yangi 🔄 xabar yuboramiz
                text = formatter(item, "UPDATE")
                msg_data = send(text, entity)
                if not msg_data:
                    raise RuntimeError("xabar hech bir adminga yetib bormadi")
                doc_info = {k: v for k, v in item.items() if k not in ("positions", "id", "updated")}
                mark_sent(entity, doc_id, updated, msg_data, doc_info)
                logger.info(f"  🔄 UPDATE {entity} {doc_id[:8]}...")
            except Exception as e:
                logger.error(f"  ❌ UPDATE {entity} {doc_id[:8]}...: {e}")

    # ── DELETE ────────────────────────────────────────────────────────────────
    deleted_items = deleted_fn(since)
    for item in deleted_items:
        doc_id = item.get("id", "")
        if not doc_id:
            continue
        try:
            existing = get_doc(entity, doc_id)
            # Allaqachon o'chirilgan — o'tkazib yuboramiz
            if existing and existing.get("updated_at") == "deleted":
                continue
            # Eski xabarni o'chiramiz
            if existing and existing["msg_data"]:
                delete_messages(existing["msg_data"])
            doc_info = (existing or {}).get("doc_info") or item
            if not send(formatter(doc_info, "DELETE"), entity):
                raise RuntimeError("xabar hech bir adminga yetib bormadi")
            mark_deleted(entity, doc_id)
            logger.info(f"  🗑 DELETE {entity} {doc_id[:8]}... ({doc_info.get('doc_number', '—')})")
        except Exception as e:
            logger.error(f"  ❌ DELETE {entity} {doc_id[:8]}...: {e}")


ALERT_AFTER_FAILURES = 3   # shuncha ketma-ket xatodan keyin super adminlarga xabar


def _notify_super_admins(text: str) -> None:
    from admin_bot.config import SUPER_ADMIN_IDS
    from admin_bot.notifier import send_plain
    try:
        send_plain(text, SUPER_ADMIN_IDS)
    except Exception as e:
        logger.error(f"Super adminga ogohlantirish yuborilmadi: {e}")


def poll_job():
    global _poll_counter
    _poll_counter += 1

    now = datetime.now(tz=timezone.utc)
    # Oyna interval*2 + 60s: polling kechiksa ham o'zgarishlar yo'qolmaydi.
    # Dublikatlardan updated_at tekshiruvi (get_doc) himoya qiladi.
    since = now - timedelta(seconds=bot_state.poll_interval * 2 + 60)

    from zoneinfo import ZoneInfo
    tashkent_time = now.astimezone(ZoneInfo("Asia/Tashkent")).strftime("%Y-%m-%d %H:%M:%S")
    logger.info(f"🔄 Polling #{_poll_counter} — {tashkent_time} (Tashkent)")

    failed = []
    for entity, (poller, deleted_fn, formatter) in ENTITIES.items():
        try:
            _process_entity(entity, poller, deleted_fn, formatter, since)
        except Exception as e:
            failed.append(entity)
            logger.error(f"{entity} polling xatolik: {e}")

    if failed:
        bot_state.consecutive_failures += 1
        if (bot_state.consecutive_failures >= ALERT_AFTER_FAILURES
                and not bot_state.alert_sent):
            bot_state.alert_sent = True
            _notify_super_admins(
                "⚠️ *MoySklad bilan aloqa muammosi*\n"
                f"{bot_state.consecutive_failures} marta ketma-ket xato: {', '.join(failed)}.\n"
                "Loglarni tekshiring."
            )
    else:
        if bot_state.alert_sent:
            _notify_super_admins("✅ MoySklad bilan aloqa tiklandi.")
        bot_state.consecutive_failures = 0
        bot_state.alert_sent = False
        bot_state.last_poll_time = now
    logger.info("✅ Polling tugadi" if not failed else f"⚠️ Polling xato bilan tugadi: {failed}")


# ── Async scheduler loop ──────────────────────────────────────────────────────

async def scheduler_loop():
    """Polling ni asyncio loop da ishlatadi."""
    logger.info(f"🚀 Scheduler ishga tushdi | Interval: {bot_state.poll_interval // 60} daq")

    # Birinchi marta darhol
    await _safe_poll()

    while True:
        await asyncio.sleep(bot_state.poll_interval)

        if bot_state.paused:
            logger.info("⏸ Polling pauza holatida, o'tkazib yuborildi")
            continue

        # Interval o'zgartirilgan bo'lsa log
        if bot_state.interval_changed:
            bot_state.interval_changed = False
            logger.info(f"⏱ Yangi interval: {bot_state.poll_interval // 60} daq")

        await _safe_poll()


async def _safe_poll():
    """poll_job kutilmagan xatoda scheduler'ni o'ldirmasin."""
    try:
        await asyncio.to_thread(poll_job)
    except Exception as e:
        logger.exception(f"poll_job kutilmagan xatolik: {e}")


# ── Main ──────────────────────────────────────────────────────────────────────

async def run_bot_forever():
    """Bot polling o'lsa qayta ishga tushiradi."""
    while True:
        try:
            await run_bot()
        except Exception as e:
            logger.error(f"Bot polling xatolik: {e}, 5 soniyadan keyin qayta urinadi...")
            await asyncio.sleep(5)


async def main():
    _check_config()
    init_db()

    logger.info("🚀 Admin bot ishga tushdi")

    await asyncio.gather(
        run_bot_forever(),
        scheduler_loop(),
    )


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logger.info("⛔ Admin bot to'xtatildi")
