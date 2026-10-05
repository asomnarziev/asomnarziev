# Serverga o'rnatish (Ubuntu/Debian, systemd)

```bash
sudo useradd -r -m freedom
sudo apt install -y python3-venv git
sudo git clone https://github.com/asomnarziev/asomnarziev.git /opt/freedom-admin-bot
cd /opt/freedom-admin-bot
sudo git checkout <branch>            # PR #2 main ga merge bo'lgach kerak emas
sudo python3 -m venv venv
sudo venv/bin/pip install -r admin_bot/requirements.txt

# .env — loyiha ildizida (admin_bot/.env.example ga qarang)
sudo cp admin_bot/.env.example .env
sudo nano .env                        # token va chat ID larni yozing
sudo chmod 600 .env
sudo chown -R freedom:freedom /opt/freedom-admin-bot

sudo cp deploy/freedom-admin-bot.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now freedom-admin-bot
journalctl -u freedom-admin-bot -f    # loglar
```

`db.sqlite3` loyiha ildizida avtomatik yaratiladi (zaxira nusxa oling).
Yangilash: `git pull && sudo systemctl restart freedom-admin-bot`.
