#!/usr/bin/env bash
# Cloudflare proxy (to'q sariq bulut) yoqilganda foydalanuvchining HAQIQIY IP'sini tiklaydi.
# Busiz ilova hamma foydalanuvchini Cloudflare IP'si deb ko'radi va kirishdagi brute-force himoyasi (IP bo'yicha) hammani bloklab qo'yishi mumkin.
#
# Faqat Cloudflare'ning o'z IP diapazonlaridan kelgan so'rovlardagina `CF-Connecting-IP` ga ishonamiz (nginx real_ip moduli),
# shuning uchun begona odam bu sarlavhani soxtalashtira olmaydi.
# Ishlatish (root):  bash /opt/pbxbot/saas/deploy/cloudflare-realip.sh      (diapazonlar o'zgarsa qayta ishga tushiring)
set -euo pipefail

OUT="${OUT:-/etc/nginx/conf.d/cloudflare-realip.conf}"
BASE="${CF_URL_BASE:-https://www.cloudflare.com}"
TMP=$(mktemp)
trap 'rm -f "$TMP"' EXIT

{
  echo "# Avtomatik yaratilgan (deploy/cloudflare-realip.sh, $(date -u +%F)). Manba: $BASE/ips/"
  count=0
  for list in ips-v4 ips-v6; do
    while read -r cidr; do
      [ -z "$cidr" ] && continue
      # Faqat CIDR ko'rinishidagi qatorlar: yuklangan matn nginx sozlamasiga begona buyruq qo'sha olmasin
      [[ "$cidr" =~ ^[0-9a-fA-F:.]+/[0-9]{1,3}$ ]] || { echo "XATO: noto'g'ri qator: $cidr" >&2; exit 1; }
      echo "set_real_ip_from $cidr;"
      count=$((count + 1))
    done < <(curl -fsS --max-time 20 "$BASE/$list")
  done
  [ "$count" -ge 10 ] || { echo "XATO: faqat $count ta diapazon olindi, to'xtatildi" >&2; exit 1; }
  echo "real_ip_header CF-Connecting-IP;"
} > "$TMP"

[ -f "$OUT" ] && cp "$OUT" "$OUT.bak"
install -m 644 "$TMP" "$OUT"

if [ -z "${SKIP_RELOAD:-}" ]; then
  if nginx -t; then
    systemctl reload nginx
    echo "OK: $(grep -c set_real_ip_from "$OUT") ta diapazon, nginx qayta yuklandi"
  else
    if [ -f "$OUT.bak" ]; then mv "$OUT.bak" "$OUT"; else rm -f "$OUT"; fi
    echo "XATO: nginx sozlamasi yaroqsiz, o'zgarish bekor qilindi" >&2
    exit 1
  fi
else
  echo "OK (nginx tegilmadi): $(grep -c set_real_ip_from "$OUT") ta diapazon -> $OUT"
fi
