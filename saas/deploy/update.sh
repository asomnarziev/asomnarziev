#!/usr/bin/env bash
# Yangilash (root): kodni olish -> bog'liqliklar -> xizmat fayli -> qayta ishga tushirish -> sog'liq tekshiruvi.
# Ishlatish:  bash /opt/pbxbot/saas/deploy/update.sh [branch]
set -euo pipefail

APP=/opt/pbxbot
cd "$APP"
git config --global --add safe.directory "$APP" 2>/dev/null || true

BRANCH="${1:-$(git rev-parse --abbrev-ref HEAD)}"
BEFORE=$(git rev-parse --short HEAD)
git fetch -q origin
git checkout -q "$BRANCH"
git pull -q --ff-only origin "$BRANCH"
AFTER=$(git rev-parse --short HEAD)
echo "Kod: $BEFORE -> $AFTER ($BRANCH)"

"$APP/venv/bin/pip" install -q -r saas/requirements.txt

# systemd xizmat fayli o'zgargan bo'lsa yangilaymiz
if ! cmp -s saas/deploy/pbxbot.service /etc/systemd/system/pbxbot.service; then
  install -m 644 saas/deploy/pbxbot.service /etc/systemd/system/pbxbot.service
  systemctl daemon-reload
  echo "systemd xizmat fayli yangilandi"
fi

# zaxira jadvali (cron) o'zgargan bo'lsa yangilaymiz
if ! cmp -s saas/deploy/pbxbot-backup.cron /etc/cron.d/pbxbot-backup; then
  install -m 644 saas/deploy/pbxbot-backup.cron /etc/cron.d/pbxbot-backup
  echo "zaxira jadvali yangilandi (har kuni 23:59 Toshkent)"
fi

chown -R pbxbot:pbxbot "$APP"
systemctl restart pbxbot

for _ in $(seq 1 20); do
  if curl -fsS http://127.0.0.1:8000/healthz >/dev/null 2>&1; then
    echo "OK: $(curl -fsS http://127.0.0.1:8000/healthz)"
    exit 0
  fi
  sleep 1
done
echo "XATO: ilova 20 soniyada javob bermadi. Loglar:" >&2
journalctl -u pbxbot -n 40 --no-pager >&2
echo "Orqaga qaytarish:  cd $APP && git checkout $BEFORE && systemctl restart pbxbot" >&2
exit 1
