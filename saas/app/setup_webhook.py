"""Telegram webhook'ni o'rnatadi:  python -m app.setup_webhook"""

from . import telegram

print(telegram.set_webhook())
