# OnlinePBX → Telegram bot

OnlinePBX'dagi qo'ng'iroq yozuvlarini shablon bo'yicha (O'zbekcha / Русский) Telegram chatlarga yuboradi.

- `TELEGRAM_CHAT_IDS` da chat ID va boshlang'ich til beriladi (`id:uz,id:ru`).
- Botda `/start` yoki `/lang` — til tanlash tugmalari; tanlov `state.json` ga saqlanadi.
- Ruxsat etilmagan chat ID ga bot o'sha ID ni qaytaradi (sozlash qulay bo'lishi uchun).
- Shablonlar: `templates.py`.

```
pip install -r requirements.txt
set -a; . ./.env; set +a
python bot.py
```
