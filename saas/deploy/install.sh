#!/usr/bin/env bash
# Ubuntu/Debian, root sifatida:  bash deploy/install.sh   (repo /opt/pbxbot ga clone qilingan bo'lishi kerak)
set -euo pipefail
APP=/opt/pbxbot
[ -f "$APP/saas/.env" ] || { echo "Avval $APP/saas/.env ni to'ldiring (.env.example dan nusxa)"; exit 1; }

apt-get update
apt-get install -y python3 python3-venv postgresql nginx certbot python3-certbot-nginx curl cron
id pbxbot &>/dev/null || useradd --system --home $APP --shell /usr/sbin/nologin pbxbot

# Postgres: parol .env dagi DATABASE_URL dan olinadi
DBURL=$(grep -E '^DATABASE_URL=' $APP/saas/.env | cut -d= -f2-)
DBPASS=$(echo "$DBURL" | sed -E 's#.*://[^:]+:([^@]+)@.*#\1#')
sudo -u postgres psql -tc "SELECT 1 FROM pg_roles WHERE rolname='pbxbot'" | grep -q 1 \
  || sudo -u postgres psql -c "CREATE ROLE pbxbot LOGIN PASSWORD '$DBPASS'"
sudo -u postgres psql -tc "SELECT 1 FROM pg_database WHERE datname='pbxbot'" | grep -q 1 \
  || sudo -u postgres createdb -O pbxbot pbxbot

python3 -m venv $APP/venv
$APP/venv/bin/pip install -q -r $APP/saas/requirements.txt
chown -R pbxbot:pbxbot $APP; chmod 600 $APP/saas/.env

cp $APP/saas/deploy/pbxbot.service /etc/systemd/system/
systemctl daemon-reload && systemctl enable --now pbxbot

# Kunlik zaxira (03:15)
cp $APP/saas/deploy/pbxbot-backup.cron /etc/cron.d/pbxbot-backup
chmod 644 /etc/cron.d/pbxbot-backup

echo "Xizmat ishga tushdi. Tekshirish: curl http://127.0.0.1:8000/healthz"
echo "Keyin: nginx.conf, certbot, setup_webhook, birinchi admin (DEPLOY.md)."
