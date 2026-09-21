#!/usr/bin/env python3
"""
Парсит публичные веб-превью Telegram-каналов (t.me/s/<username>) и
сохраняет статистику в tgstats.json в корне репозитория.

Запускается по расписанию через GitHub Actions
(.github/workflows/update-stats.yml) прямо на серверах GitHub —
поэтому браузеру никогда не приходится ходить в Telegram напрямую
и упираться в CORS/прокси. Страница просто читает готовый json.

Устроено по образцу scripts/fetch_stats.py с резюме-сайта, но:
  - каждому каналу добавлено поле "group" (для группировки на странице)
  - вместо текста последнего поста сохраняется список меток времени
    всех постов с превью-страницы (post_times) — по ним на странице
    считается "постов за месяц/неделю/день" и квота
"""

import json
import re
import sys
from datetime import datetime, timezone

import requests
from bs4 import BeautifulSoup

# username -> группа на странице
STAT_CHANNELS = [
    {"username": "IdentityV_Official",     "group": "donatov"},
    {"username": "BloodStrike_OfficialRu", "group": "donatov"},
    {"username": "RobloxRu_Official",      "group": "donatov"},
    {"username": "RustMobileInfo",         "group": "vavinews"},
    {"username": "valorantmobile",         "group": "vavinews"},
    {"username": "deltaforce_ru",          "group": "vavinews"},
    {"username": "pubg_mobile_ruhub",      "group": "ldshop"},
    {"username": "steam_news_ruhub",       "group": "ldshop"},
    {"username": "play_rustmobile",        "group": "tencent"},
]

HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; StatsBot/1.0; +https://github.com/)"
}


def parse_count(raw: str):
    if not raw:
        return None
    raw = raw.strip().upper().replace(" ", "").replace(",", "").replace("\u00a0", "")
    mult = 1
    if raw.endswith("K"):
        mult = 1_000
        raw = raw[:-1]
    elif raw.endswith("M"):
        mult = 1_000_000
        raw = raw[:-1]
    try:
        return round(float(raw) * mult)
    except ValueError:
        return None


def fetch_channel(username: str, group: str):
    url = f"https://t.me/s/{username}"
    try:
        resp = requests.get(url, headers=HEADERS, timeout=15)
        resp.raise_for_status()
    except requests.RequestException as e:
        print(f"[!] {username}: ошибка запроса — {e}", file=sys.stderr)
        return {
            "username": username,
            "group": group,
            "title": f"@{username}",
            "avatar": None,
            "subs": None,
            "subs_raw": None,
            "ok": False,
            "last_post_date": None,
            "post_times": [],
        }

    soup = BeautifulSoup(resp.text, "html.parser")

    title_node = soup.select_one(".tgme_channel_info_header_title")
    title = title_node.get_text(strip=True) if title_node else f"@{username}"

    avatar_img = soup.select_one(".tgme_page_photo_image img")
    avatar = avatar_img["src"] if avatar_img and avatar_img.has_attr("src") else None

    subs_raw = None
    for counter in soup.select(".tgme_channel_info_counter"):
        type_node = counter.select_one(".counter_type")
        ctype = type_node.get_text(strip=True).lower() if type_node else ""
        if "subscriber" in ctype or "member" in ctype:
            val_node = counter.select_one(".counter_value")
            subs_raw = val_node.get_text(strip=True) if val_node else None

    subs = parse_count(subs_raw)
    ok = subs is not None
    if not ok:
        print(f"[!] {username}: не удалось распарсить число подписчиков", file=sys.stderr)

    # Превью-страница отдаёт ~20 последних сообщений — собираем все метки
    # времени, чтобы на странице посчитать посты за месяц/неделю/день.
    post_times = []
    for time_node in soup.select(".tgme_widget_message_date time"):
        dt = time_node.get("datetime")
        if dt:
            post_times.append(dt)

    last_post_date = post_times[-1] if post_times else None

    return {
        "username": username,
        "group": group,
        "title": title,
        "avatar": avatar,
        "subs": subs,
        "subs_raw": subs_raw,
        "ok": ok,
        "last_post_date": last_post_date,
        "post_times": post_times,
    }


def main():
    results = [fetch_channel(c["username"], c["group"]) for c in STAT_CHANNELS]
    total = sum(c["subs"] for c in results if c["subs"] is not None)

    payload = {
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "total": total,
        "channels": results,
    }

    with open("tgstats.json", "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)

    print(f"OK: {len(results)} каналов, всего подписчиков {total}")


if __name__ == "__main__":
    main()
