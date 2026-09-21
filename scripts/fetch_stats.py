#!/usr/bin/env python3
"""
Парсит публичные веб-превью Telegram-каналов (t.me/s/<username>) и
сохраняет статистику в tgstats.json в корне репозитория.

Запускается по расписанию через GitHub Actions
(.github/workflows/update-stats.yml) прямо на серверах GitHub.

Считаются ВСЕ посты за последние WINDOW_DAYS (30) дней: превью-страница
отдаёт только ~20 последних сообщений, поэтому скрипт листает историю
назад через t.me/s/<username>?before=<id>, пока не дойдёт до поста старше
30 дней (или до начала канала).
"""

import json
import sys
import time
from datetime import datetime, timedelta, timezone

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

WINDOW_DAYS = 30     # за сколько дней считаем посты
MAX_PAGES = 150      # предохранитель: ~20 постов на страницу
PAGE_DELAY = 0.7     # пауза между запросами, чтобы не словить лимит


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


def get_page(url: str, retries: int = 3) -> requests.Response:
    for attempt in range(retries):
        try:
            resp = requests.get(url, headers=HEADERS, timeout=15)
            resp.raise_for_status()
            return resp
        except requests.RequestException:
            if attempt == retries - 1:
                raise
            time.sleep(2 * (attempt + 1))


def extract_posts(soup):
    """Возвращает список (id, iso_строка, datetime) по всем сообщениям страницы."""
    items = []
    for msg in soup.select(".tgme_widget_message[data-post]"):
        try:
            post_id = int(msg["data-post"].rsplit("/", 1)[-1])
        except (ValueError, KeyError):
            continue
        time_node = msg.select_one(".tgme_widget_message_date time")
        iso = time_node.get("datetime") if time_node else None
        if not iso:
            continue
        try:
            dt = datetime.fromisoformat(iso)
        except ValueError:
            continue
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        items.append((post_id, iso, dt))
    return items


def empty_result(username: str, group: str):
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


def fetch_channel(username: str, group: str):
    base = f"https://t.me/s/{username}"
    try:
        resp = get_page(base)
    except requests.RequestException as e:
        print(f"[!] {username}: ошибка запроса — {e}", file=sys.stderr)
        return empty_result(username, group)

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

    # ---- листаем историю назад до границы WINDOW_DAYS ----
    cutoff = datetime.now(timezone.utc) - timedelta(days=WINDOW_DAYS)
    posts = {}          # id -> (iso, dt), дедупликация по id
    prev_min_id = None
    page = soup
    pages_loaded = 0

    for _ in range(MAX_PAGES):
        items = extract_posts(page)
        if not items:
            break
        pages_loaded += 1
        for pid, iso, dt in items:
            posts[pid] = (iso, dt)

        min_id = min(pid for pid, _, _ in items)
        oldest_dt = min(dt for _, _, dt in items)

        if oldest_dt < cutoff:      # дошли до постов старше 30 дней
            break
        if min_id <= 1:             # начало канала
            break
        if prev_min_id is not None and min_id >= prev_min_id:
            break                   # страница не сдвинулась — защита от цикла
        prev_min_id = min_id

        time.sleep(PAGE_DELAY)
        try:
            page = BeautifulSoup(get_page(f"{base}?before={min_id}").text, "html.parser")
        except requests.RequestException as e:
            print(f"[!] {username}: не удалось загрузить старые посты — {e}", file=sys.stderr)
            break
    else:
        print(f"[!] {username}: достигнут лимит {MAX_PAGES} страниц", file=sys.stderr)

    in_window = sorted(
        (v for v in posts.values() if v[1] >= cutoff), key=lambda v: v[1]
    )
    post_times = [iso for iso, _ in in_window]
    last_post_date = max(posts.values(), key=lambda v: v[1])[0] if posts else None

    print(f"    {username}: {len(post_times)} постов за {WINDOW_DAYS} дн ({pages_loaded} стр.)")

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
  
