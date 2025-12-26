# scripts/update.py
import json
import os
import re
from datetime import datetime, timezone, timedelta

import requests

URLS = [
    {
        "platform": "BookWalker",
        "url": "https://www.bookwalker.com.tw/event",
        "note": "主題&活動列表",
    },
    {
        "platform": "Readmoo",
        "url": "https://readmoo.com/campaign/activities",
        "note": "進行中活動",
    },
    {
        "platform": "HyRead",
        "url": "https://ebook.hyread.com.tw/Template/store/event_list.jsp",
        "note": "熱門活動",
    },
    {
        "platform": "Pubu",
        "url": "https://www.pubu.com.tw/activity/ongoing",
        "note": "全站活動",
    },
    {
        "platform": "Kobo",
        "url": "https://www.kobo.com/tw/zh",
        "note": "折扣多在主頁（弱來源）",
    },
    {
        "platform": "博客來",
        "url": "https://activity.books.com.tw/crosscat/show/A00000062854?loc=mood_001",
        "note": "電子書活動入口（可能會調整）",
    },
]

OUT_JSON = "data/deals.json"
OUT_HTML = "index.html"


def extract_title(html: str) -> str:
    # 盡量抓 <title>，再清理空白
    m = re.search(r"<title[^>]*>(.*?)</title>", html, re.IGNORECASE | re.DOTALL)
    if not m:
        return ""
    title = re.sub(r"\s+", " ", m.group(1)).strip()
    return title


def fetch_title(url: str) -> str:
    headers = {
        "User-Agent": "Mozilla/5.0 (compatible; ebook-promotions-bot/1.0)"
    }
    r = requests.get(url, headers=headers, timeout=25)
    r.raise_for_status()
    r.encoding = r.apparent_encoding or "utf-8"
    title = extract_title(r.text)
    return title


def main():
    tz = timezone(timedelta(hours=8))  # 台灣時間
    now = datetime.now(tz).strftime("%Y-%m-%d %H:%M")

    items = []
    for x in URLS:
        title = ""
        error = ""
        try:
            title = fetch_title(x["url"])
        except Exception as e:
            error = str(e)

        items.append(
            {
                "platform": x["platform"],
                "url": x["url"],
                "note": x["note"],
                "page_title": title,
                "error": error,
            }
        )

    os.makedirs("data", exist_ok=True)

    payload = {
        "updated_at_taipei": now,
        "items": items,
    }

    with open(OUT_JSON, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)

    # 產生最簡單的一頁 HTML（你之後要美化再說）
    html_lines = []
    html_lines.append("<!doctype html>")
    html_lines.append('<html lang="zh-Hant">')
    html_lines.append("<head>")
    html_lines.append('<meta charset="utf-8" />')
    html_lines.append('<meta name="viewport" content="width=device-width, initial-scale=1" />')
    html_lines.append("<title>電子書平台活動快照</title>")
    html_lines.append("</head>")
    html_lines.append("<body style='font-family: -apple-system, BlinkMacSystemFont, Segoe UI, Roboto, Noto Sans TC, Helvetica, Arial; line-height:1.6; padding:16px; max-width: 900px; margin: 0 auto;'>")
    html_lines.append("<h1>📚 電子書平台活動快照</h1>")
    html_lines.append(f"<p>更新時間（台灣）：<b>{payload['updated_at_taipei']}</b></p>")
    html_lines.append("<hr/>")

    for it in items:
        html_lines.append(f"<h2>{it['platform']}</h2>")
        if it["page_title"]:
            html_lines.append(f"<p>頁面標題：{it['page_title']}</p>")
        if it["note"]:
            html_lines.append(f"<p>備註：{it['note']}</p>")
        html_lines.append(f"<p><a href='{it['url']}' target='_blank' rel='noopener noreferrer'>→ 點我查看活動</a></p>")
        if it["error"]:
            html_lines.append(f"<p style='color:#b00020;'>（抓取失敗：{it['error']}）</p>")
        html_lines.append("<hr/>")

    html_lines.append("<p style='font-size: 12px; opacity: 0.7;'>v1 只彙整官方活動入口，不計算券後價與單書特價。</p>")
    html_lines.append("</body></html>")

    with open(OUT_HTML, "w", encoding="utf-8") as f:
        f.write("\n".join(html_lines))


if __name__ == "__main__":
    main()
