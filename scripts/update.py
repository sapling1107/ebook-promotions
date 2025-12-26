# scripts/update.py
import json
import os
import re
from datetime import datetime, timezone, timedelta
from typing import List, Dict, Any

import requests
from bs4 import BeautifulSoup

URLS = [
    {
        "platform": "BookWalker",
        "url": "https://www.bookwalker.com.tw/event",
        "note": "主題&活動列表",
        "extra": "bw",
    },
    {
        "platform": "Readmoo",
        "url": "https://readmoo.com/campaign/activities",
        "note": "進行中活動",
        "extra": "readmoo",
    },
    {
        "platform": "HyRead",
        "url": "https://ebook.hyread.com.tw/Template/store/event_list.jsp",
        "note": "熱門活動",
        "extra": None,
    },
    {
        "platform": "Pubu",
        "url": "https://www.pubu.com.tw/activity/ongoing",
        "note": "全站活動",
        "extra": None,
    },
    {
        "platform": "Kobo",
        "url": "https://www.kobo.com/tw/zh",
        "note": "主頁（弱來源）",
        "extra": None,
    },
    {
        "platform": "博客來",
        "url": "https://activity.books.com.tw/crosscat/show/A00000062854?loc=mood_001",
        "note": "電子書活動入口（可能會調整）",
        "extra": None,
    },
]

OUT_JSON = "data/deals.json"
OUT_HTML = "index.html"


def extract_title(html: str) -> str:
    m = re.search(r"<title[^>]*>(.*?)</title>", html, re.IGNORECASE | re.DOTALL)
    if not m:
        return ""
    return re.sub(r"\s+", " ", m.group(1)).strip()


def fetch_html(url: str) -> Dict[str, Any]:
    headers = {
        "User-Agent": "Mozilla/5.0 (compatible; ebook-promotions-bot/1.0)"
    }
    r = requests.get(url, headers=headers, timeout=25)
    status = r.status_code
    r.raise_for_status()
    r.encoding = r.apparent_encoding or "utf-8"
    return {"text": r.text, "status": status}


def pick_unique_texts(texts: List[str], limit: int = 8) -> List[str]:
    out = []
    seen = set()
    for t in texts:
        t = re.sub(r"\s+", " ", (t or "")).strip()
        if not t:
            continue
        if len(t) < 3:
            continue
        if t in seen:
            continue
        seen.add(t)
        out.append(t)
        if len(out) >= limit:
            break
    return out


def extract_bw_cards(html: str) -> List[str]:
    soup = BeautifulSoup(html, "html.parser")

    # 先抓「看起來像活動卡片/列表」的連結文字
    candidates = []

    # 常見：活動列表會有很多 <a> 的可見文字
    for a in soup.select("a"):
        txt = a.get_text(" ", strip=True)
        # 過濾掉導覽、登入、常見無意義連結
        if not txt:
            continue
        if txt in {"點我查看活動", "更多", "返回", "登入", "註冊"}:
            continue
        # 過濾過長的段落型文字
        if len(txt) > 60:
            continue
        candidates.append(txt)

    # 再用一些常見關鍵字提升命中率（不硬性依賴）
    boosted = [t for t in candidates if any(k in t for k in ["折", "滿", "會員", "優惠", "活動", "書展", "限定", "回饋"])]
    merged = boosted + candidates

    return pick_unique_texts(merged, limit=10)


def extract_readmoo_cards(html: str) -> List[str]:
    soup = BeautifulSoup(html, "html.parser")
    candidates = []

    # Readmoo 活動頁通常有活動卡片標題，直接抓所有 <a>/<h*> 的短文字
    for tag in soup.select("h1, h2, h3, h4, a"):
        txt = tag.get_text(" ", strip=True)
        if not txt:
            continue
        if txt in {"點我查看活動", "更多", "返回", "登入", "註冊"}:
            continue
        if len(txt) > 60:
            continue
        candidates.append(txt)

    boosted = [t for t in candidates if any(k in t for k in ["折", "滿", "會員", "優惠", "活動", "書展", "回饋", "限時"])]
    merged = boosted + candidates
    return pick_unique_texts(merged, limit=10)


def load_prev_signature() -> Dict[str, str]:
    if not os.path.exists(OUT_JSON):
        return {}
    try:
        with open(OUT_JSON, "r", encoding="utf-8") as f:
            prev = json.load(f)
        sig = {}
        for it in prev.get("items", []):
            platform = it.get("platform", "")
            signature = it.get("signature", "")
            if platform:
                sig[platform] = signature
        return sig
    except Exception:
        return {}


def make_signature(page_title: str, card_titles: List[str], status: int, error: str) -> str:
    # 用「最能代表今日狀態」的資訊做簽名
    base = {
        "status": status,
        "title": page_title or "",
        "cards": card_titles[:8],
        "error": (error or "")[:120],
    }
    return json.dumps(base, ensure_ascii=False, sort_keys=True)


def main():
    tz = timezone(timedelta(hours=8))  # 台灣時間
    now = datetime.now(tz).strftime("%Y-%m-%d %H:%M")

    prev_sig = load_prev_signature()

    items = []
    changed_platforms = []

    for x in URLS:
        title = ""
        error = ""
        status = 0
        card_titles: List[str] = []

        try:
            res = fetch_html(x["url"])
            html = res["text"]
            status = res["status"]
            title = extract_title(html)

            if x.get("extra") == "bw":
                card_titles = extract_bw_cards(html)
            elif x.get("extra") == "readmoo":
                card_titles = extract_readmoo_cards(html)

        except requests.HTTPError as e:
            # 例如 403
            error = str(e)
            try:
                status = e.response.status_code if e.response is not None else 0
            except Exception:
                status = 0
        except Exception as e:
            error = str(e)

        signature = make_signature(title, card_titles, status, error)

        if prev_sig.get(x["platform"]) and prev_sig.get(x["platform"]) != signature:
            changed_platforms.append(x["platform"])

        items.append(
            {
                "platform": x["platform"],
                "url": x["url"],
                "note": x["note"],
                "page_title": title,
                "card_titles": card_titles,
                "http_status": status,
                "error": error,
                "signature": signature,
            }
        )

    os.makedirs("data", exist_ok=True)

    payload = {
        "updated_at_taipei": now,
        "has_new_changes": "是" if len(changed_platforms) > 0 else "否",
        "changed_platforms": changed_platforms,
        "items": items,
    }

    with open(OUT_JSON, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)

    # HTML 一頁清單（含弱化 403）
    html_lines = []
    html_lines.append("<!doctype html>")
    html_lines.append('<html lang="zh-Hant">')
    html_lines.append("<head>")
    html_lines.append('<meta charset="utf-8" />')
    html_lines.append('<meta name="viewport" content="width=device-width, initial-scale=1" />')
    html_lines.append("<title>電子書平台活動快照</title>")
    html_lines.append("</head>")
    html_lines.append("<body style='font-family:-apple-system,BlinkMacSystemFont,Segoe UI,Roboto,Noto Sans TC,Helvetica,Arial;line-height:1.65;padding:16px;max-width:980px;margin:0 auto;'>")
    html_lines.append("<h1 style='margin:0 0 8px;'>📚 電子書平台活動快照</h1>")
    html_lines.append(f"<p style='margin:0 0 8px;'>更新時間（台灣）：<b>{payload['updated_at_taipei']}</b></p>")
    html_lines.append(f"<p style='margin:0 0 16px;'>今天是否有新增活動：<b>{payload['has_new_changes']}</b>"
                      + (f"（變動：{', '.join(payload['changed_platforms'])}）" if payload["changed_platforms"] else "")
                      + "</p>")
    html_lines.append("<hr style='opacity:.35'/>")

    for it in items:
        is_403 = (it.get("http_status") == 403) or ("403" in (it.get("error") or ""))
        # 弱化：403 變灰 + 降低透明度
        wrap_style = "opacity:.45; filter: grayscale(1);" if is_403 else "opacity:1;"
        title_style = "color:#555;" if is_403 else "color:#111;"

        html_lines.append(f"<section style='{wrap_style} padding:8px 0;'>")
        html_lines.append(f"<h2 style='margin:6px 0; {title_style}'>{it['platform']}</h2>")

        if it["page_title"]:
            html_lines.append(f"<p style='margin:4px 0;'>頁面標題：{it['page_title']}</p>")
        if it["note"]:
            html_lines.append(f"<p style='margin:4px 0;'>備註：{it['note']}</p>")

        # 額外抓卡片標題（只在 BW/Readmoo 有）
        if it.get("card_titles"):
            html_lines.append("<div style='margin:8px 0 6px;'><b>活動卡片（擷取）</b></div>")
            html_lines.append("<ul style='margin:6px 0 10px 18px;'>")
            for t in it["card_titles"][:10]:
                html_lines.append(f"<li>{t}</li>")
            html_lines.append("</ul>")

        html_lines.append(f"<p style='margin:6px 0;'><a href='{it['url']}' target='_blank' rel='noopener noreferrer'>→ 點我查看活動</a></p>")

        if it["error"]:
            html_lines.append(f"<p style='margin:6px 0; color:#b00020;'>（抓取失敗：{it['error']}）</p>")

        html_lines.append("</section>")
        html_lines.append("<hr style='opacity:.25'/>")

    html_lines.append("<p style='font-size:12px;opacity:.7;margin-top:14px;'>v1 只彙整官方活動入口；不計券後價與單書特價。403 平台已自動弱化顯示。</p>")
    html_lines.append("</body></html>")

    with open(OUT_HTML, "w", encoding="utf-8") as f:
        f.write("\n".join(html_lines))


if __name__ == "__main__":
    main()
