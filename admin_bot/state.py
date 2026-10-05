"""
admin_bot/state.py
------------------
Bot va scheduler o'rtasida umumiy holat.
"""

from datetime import datetime
from admin_bot.config import POLL_INTERVAL_SECONDS

paused: bool = False
poll_interval: int = POLL_INTERVAL_SECONDS
interval_changed: bool = False
last_poll_time: datetime | None = None
debtors_page: dict = {}  # chat_id → current page
consecutive_failures: int = 0   # ketma-ket muvaffaqiyatsiz polling soni
alert_sent: bool = False        # 'MoySklad ishlamayapti' xabari yuborilganmi
