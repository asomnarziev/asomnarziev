#!/usr/bin/env bash
# Zaxira (root): PostgreSQL bazasi + yuklangan cheklar; baza shifrlab adminning Telegramiga yuboriladi.
#
# Tartib: baza dump -> to'liqligini tekshirish -> cheklar arxivi -> ESKI nusxalarni o'chirish -> Telegram.
# Eskilari faqat YANGI zaxira tekshiruvdan o'tgandan keyin o'chiriladi, shuning uchun buzuq dump yaxshi nusxani yo'q qilmaydi.
# Telegramda ham kechagi xabar yangisi yetib borgandan keyingina o'chiriladi (app/backup_cli.py).
#
# Ishlatish:  bash /opt/pbxbot/saas/deploy/backup.sh        (kunlik cron: deploy/pbxbot-backup.cron)
# Sozlamalar (muhit): BACKUP_DIR, BACKUP_KEEP (nechta eng yangi nusxa qolsin, standart 1), SKIP_TELEGRAM=1
set -euo pipefail

APP="${APP:-/opt/pbxbot}"
DEST="${BACKUP_DIR:-/var/backups/pbxbot}"
KEEP="${BACKUP_KEEP:-1}"
PY="${PY:-$APP/venv/bin/python}"
[[ "$KEEP" =~ ^[1-9][0-9]*$ ]] || KEEP=1

umask 077
mkdir -p "$DEST"
STAMP=$(date +%F_%H%M)
DB_FILE="$DEST/db_$STAMP.sql.gz"
PART="$DB_FILE.part"
trap 'rm -f "$PART"' EXIT

# 1) baza: avval vaqtinchalik faylga, tekshiruvdan keyin asl nomga
sudo -u postgres pg_dump pbxbot | gzip > "$PART"
gzip -t "$PART"
TAIL=$(zcat "$PART" | tail -n 5)
if ! grep -q "PostgreSQL database dump complete" <<<"$TAIL"; then
  echo "XATO: baza zaxirasi to'liq emas (dump oxiri yo'q), eski nusxalar tegilmadi" >&2
  exit 1
fi
mv "$PART" "$DB_FILE"

# 2) yuklangan cheklar
if [ -d "$APP/saas/uploads" ]; then
  tar -czf "$DEST/uploads_$STAMP.tar.gz" -C "$APP/saas" uploads
fi

# 3) eski nusxalarni o'chirish (faqat eng yangi KEEP tasi qoladi)
prune() {
  # eng yangilari birinchi (o'zgartirilgan vaqt bo'yicha); KEEP tasidan keyingilari o'chadi
  find "$DEST" -maxdepth 1 -type f -name "$1" -printf '%T@ %p\n' | sort -rn | tail -n +"$((KEEP + 1))" | cut -d' ' -f2- | xargs -r rm -f --
}
prune 'db_*.sql.gz'
prune 'uploads_*.tar.gz'
echo "OK: $DB_FILE ($(du -h "$DB_FILE" | cut -f1)), saqlangan nusxalar: $KEEP"

# 4) adminga Telegram orqali (shifrlangan). Xato bo'lsa ham mahalliy zaxira saqlangan.
if [ -z "${SKIP_TELEGRAM:-}" ]; then
  rc=0
  (cd "$APP/saas" && "$PY" -m app.backup_cli telegram "$DB_FILE" --state "$DEST/telegram_state.json") || rc=$?
  [ "$rc" -eq 0 ] || echo "OGOHLANTIRISH: Telegramga yuborilmadi (kod $rc); mahalliy zaxira saqlangan" >&2
fi
