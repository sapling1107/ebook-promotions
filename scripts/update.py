# scripts/update.py
import json
import os
import re
from datetime import datetime, timezone, timedelta
from typing import List, Dict, Any

import requests
from bs4 import BeautifulSoup

PARSER_VERSION = 2
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
        "extra": "hyread",
    },
    {
        "platform": "Pubu",
        "url": "https://www.pubu.com.tw/activity/ongoing",
        "note": "全站活動",
        "extra": "pubu",
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
        "extra": "books"
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
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-TW,zh;q=0.9,en;q=0.6",
    "Cache-Control": "no-cache",
    "Pragma": "no-cache",
    }
    r = requests.get(url, headers=headers, timeout=25)
    status = r.status_code
    r.raise_for_status()
    r.encoding = r.apparent_encoding or "utf-8"
    return {"text": r.text, "status": status}


def pick_unique_texts(texts: List[str], limit: int = 8) -> List[str]:
    # 1) 基礎清理
    cleaned = []
    for t in texts:
        t = re.sub(r"\s+", " ", (t or "")).strip()
        if not t or len(t) < 4:
            continue
        cleaned.append(t)

    # 2) 先用長度排序：長的在前（資訊量通常比較高）
    cleaned = sorted(set(cleaned), key=len, reverse=True)

    # 3) 子字串去重：如果 t 完全包含在已保留的某一條裡，就丟掉
    kept = []
    for t in cleaned:
        if any(t in k for k in kept):
            continue
        kept.append(t)

    return kept[:limit]


def extract_bw_cards(html: str) -> List[str]:
    soup = BeautifulSoup(html, "html.parser")

    prev_titles = set()
    try:
        with open("data/deals.json", "r", encoding="utf-8") as f:
            prev = json.load(f)
        for it in prev.get("items", []):
            if it.get("platform") == "BookWalker":
                prev_titles = set(it.get("card_titles", []))
    except Exception:
        pass

    candidates = []
    for a in soup.select("a"):
        t = a.get_text(" ", strip=True)
        t = re.sub(r"\s+", " ", (t or "")).strip()
        if not t:
            continue
        if len(t) < 6 or len(t) > 90:
            continue

        # 導覽/系統字踢掉
        if any(bad in t for bad in [
            "會員資料", "會員通知", "登入", "註冊", "推薦主題", "活動列表", "查看更多", "下載APP"
        ]):
            continue

        candidates.append(t)

    def score(t: str) -> int:
        s = 0
        # 折扣/價格/門檻/日期：強訊號
        if re.search(r"\d+\s*折", t): s += 6
        if re.search(r"\d+\s*(%|％)", t): s += 5
        if re.search(r"滿\s*\d+", t): s += 5
        if re.search(r"特價\s*\d+|優惠價\s*\d+|\d+\s*元", t): s += 5  # 99元、2000元
        if re.search(r"\d{1,2}[./]\d{1,2}", t): s += 4
        if re.search(r"\d{4}[./]\d{1,2}[./]\d{1,2}", t): s += 4
        if any(k in t for k in ["限時", "優惠", "折價券", "回饋", "書展", "再折", "加碼", "特價"]): s += 3

        # 活動型（你剛剛點名的閱讀報告/點數券）：加分讓它不會被擠掉
        if any(k in t for k in ["閱讀報告", "點數", "領券", "優惠券", "抽獎", "任務"]): s += 6

        # 不要再用「日文書」扣分（它有時就是正常活動標題的一部分）
        # 但仍然保留對「限制級/連載」的負分以避免洗版
        if any(k in t for k in ["限制級", "連載"]): s -= 6

        return s

    scored = [(score(t), t) for t in candidates]
    scored = [(sc, t) for (sc, t) in scored if sc > 0]
    scored.sort(key=lambda x: x[0], reverse=True)
    # 新活動：今天有、昨天沒有
    new_items = [t for (_, t) in scored if t not in prev_titles]

    # 保證最多保留 2 個新活動
    guaranteed_new = new_items[:2]

    # 分兩類：促銷型 vs 活動型
    promo_like = []
    activity_like = []
    for sc, t in scored:
        if any(k in t for k in ["閱讀報告", "點數", "領券", "優惠券", "抽獎", "任務"]):
            activity_like.append(t)
        else:
            promo_like.append(t)

    # 促銷型先取 6，活動型補 2（避免漏掉你在意的活動）
    picked = guaranteed_new + promo_like

    return pick_unique_texts(picked, limit=15)


def extract_readmoo_cards(html: str) -> List[str]:
    import re
    import json

    # Readmoo 常見兩種狀態：
    # A) 有 READMOO_CAMPAIGNS（可抽）
    # B) 被擋（只有驗證/JS 提示頁） -> 抽不到
    h_lower = (html or "").lower()
    if "verify that you're not a robot" in h_lower or "enable javascript" in h_lower:
        return []

    # 放寬：抓到第一個 ]; 為止，不要求一定是 [{...}];
    m = re.search(r"const\s+READMOO_CAMPAIGNS\s*=\s*(\[[\s\S]*?\]);", html)
    if not m:
        return []

    raw = m.group(1)
    try:
        data = json.loads(raw)
    except Exception:
        return []

    cards = []
    for item in data:
        name = (item.get("name") or "").strip()
        desc = (item.get("description") or "").strip()
        start = (item.get("start_date") or "").strip()
        end = (item.get("end_date") or "").strip()

        line = " ".join(x for x in [
            name,
            desc,
            f"{start}–{end}" if start or end else ""
        ] if x)

        if line:
            cards.append(line)

    return cards

def extract_hyread_cards(html: str) -> List[str]:
    soup = BeautifulSoup(html, "html.parser")

    results = []
    seen = set()

    # HyRead 活動頁通常是一張張卡片，每張卡片內有「主標題」+「紅字副標(日期/折扣)」
    # 我們做法：從每個 <a> 往上找卡片容器，抽「最像主標」與「最像折扣/日期」的兩段。
    for a in soup.select("a[href]"):
        href = (a.get("href") or "").strip()
        if not href:
            continue

        # 這條規則用來避免抓到導覽連結；若你發現抓太少，可把這行註解掉
        if "event" not in href.lower():
            continue

        # 往上找容器（最多爬 5 層），抓一整張卡片的文字
        node = a
        container_texts = None
        for _ in range(5):
            if node and node.name in ("div", "li", "article", "section"):
                texts = [t.strip() for t in node.stripped_strings if t.strip()]
                # 卡片通常至少會有 2 段文字
                if len(texts) >= 2:
                    container_texts = texts
                    break
            node = node.parent

        if not container_texts:
            continue

        # 主標：挑「比較不像日期/折扣」且字數合理的句子
        # 副標：挑「含折/元/%/滿/日期符號」的句子
        def looks_like_meta(s: str) -> bool:
            return bool(re.search(r"(折|元|%|％|滿|再折|限時|週末|限定|\d{1,2}[./-]\d{1,2}|/)", s))

        title = ""
        subtitle = ""

        # 先找副標（通常紅字那行）
        meta_candidates = [t for t in container_texts if looks_like_meta(t) and len(t) <= 60]
        if meta_candidates:
            # 通常第一個就很像日期/折扣
            subtitle = meta_candidates[0]

        # 再找主標
        title_candidates = [t for t in container_texts if (not looks_like_meta(t)) and 4 <= len(t) <= 30]
        if title_candidates:
            title = title_candidates[0]
        else:
            # 找不到就退而求其次：取第一個短句當主標
            short = [t for t in container_texts if 4 <= len(t) <= 30]
            title = short[0] if short else ""

        title = re.sub(r"\s+", " ", title).strip()
        subtitle = re.sub(r"\s+", " ", subtitle).strip()

        if not title:
            continue

        line = f"{title}｜{subtitle}" if subtitle else title
        # 避免超長
        if len(line) > 90:
            line = line[:87] + "…"

        if line not in seen:
            seen.add(line)
            results.append(line)

    return pick_unique_texts(results, limit=12)

def extract_books_cards(html: str) -> List[str]:
    soup = BeautifulSoup(html, "html.parser")
    candidates = []

    for a in soup.select("a"):
        txt = a.get_text(" ", strip=True)
        txt = re.sub(r"\s+", " ", (txt or "")).strip()
        if not txt:
            continue
        if len(txt) < 6 or len(txt) > 80:
            continue

        if any(bad in txt for bad in ["登入", "註冊", "會員", "購物車", "客服", "更多", "返回"]):
            continue

        # 只保留比較像活動的
        if any(k in txt for k in ["折", "優惠", "活動", "書展", "特價", "回饋", "滿", "限時"]):
            candidates.append(txt)

    return pick_unique_texts(candidates, limit=12)

def extract_pubu_cards(html: str) -> List[str]:
    soup = BeautifulSoup(html, "html.parser")
    results = []
    seen = set()

    # Pubu 卡片常見有「活動期間 2025-xx-xx - 2026-xx-xx」
    date_re = re.compile(r"\d{4}-\d{2}-\d{2}")

    # 做法：找出包含日期的文字區塊，往上找卡片容器，再抽出標題
    for el in soup.find_all(string=lambda s: s and date_re.search(s)):
        txt = re.sub(r"\s+", " ", str(el)).strip()
        # 只處理看起來像「活動期間」那種
        if "活動期間" not in txt and "活動時間" not in txt and len(date_re.findall(txt)) < 2:
            continue

        # 往上找卡片容器
        node = el.parent
        container = None
        for _ in range(6):
            if not node:
                break
            if node.name in ("div", "li", "article", "section"):
                texts = [t.strip() for t in node.stripped_strings if t.strip()]
                # 卡片應該會有標題 + 期間，至少 2 段
                if len(texts) >= 2:
                    container = node
                    break
            node = node.parent

        if not container:
            continue

        texts = [t.strip() for t in container.stripped_strings if t.strip()]

        # 嘗試找標題：通常是第一個比較短、且不包含日期/活動期間的句子
        title = ""
        for t in texts:
            if len(t) <= 40 and (not date_re.search(t)) and ("活動期間" not in t) and ("活動時間" not in t):
                title = t
                break

        # 抓期間：找第一個含兩個日期的句子
        period = ""
        for t in texts:
            dates = date_re.findall(t)
            if len(dates) >= 2:
                # 可能是 "活動期間2025-..-.. - 2026-..-.."
                period = re.sub(r"\s+", " ", t).strip()
                break

        if not title:
            continue

        line = f"{title}｜{period}" if period else title
        if len(line) > 100:
            line = line[:97] + "…"

        if line not in seen:
            seen.add(line)
            results.append(line)

    return pick_unique_texts(results, limit=12)

def load_prev_signature() -> Dict[str, Any]:
    if not os.path.exists(OUT_JSON):
        return {"parser_version": None, "sig": {}}
    try:
        with open(OUT_JSON, "r", encoding="utf-8") as f:
            prev = json.load(f)
        sig = {}
        for it in prev.get("items", []):
            platform = it.get("platform", "")
            signature = it.get("signature", "")
            if platform:
                sig[platform] = signature
        return {"parser_version": prev.get("parser_version"), "sig": sig}
    except Exception:
        return {"parser_version": None, "sig": {}}


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

    prev = load_prev_signature()
    prev_sig = prev["sig"]
    prev_ver = prev["parser_version"]

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

            # 🔎 DEBUG：HTML 太短時存檔（判斷是否被擋）
            if html and len(html) < 2000:
                from pathlib import Path
                slug = x["platform"].lower()
                Path(f"debug_{slug}.html").write_text(html, encoding="utf-8")

            if x.get("extra") == "bw":
                card_titles = extract_bw_cards(html)
            elif x.get("extra") == "readmoo":
                card_titles = extract_readmoo_cards(html)
            elif x.get("extra") == "hyread":
                card_titles = extract_hyread_cards(html)
            elif x.get("extra") == "books":
                card_titles = extract_books_cards(html)
            elif x.get("extra") == "pubu":
                card_titles = extract_pubu_cards(html)

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

        if (
            prev_ver == PARSER_VERSION
            and prev_sig.get(x["platform"])
            and prev_sig.get(x["platform"]) != signature
        ):
            changed_platforms.append(x["platform"])

        if x["platform"] == "Readmoo":
            # 如果抽不到活動，順便判斷是不是被擋
            if ("READMOO_CAMPAIGNS" not in html) and (
                "verify that you're not a robot" in html.lower()
                or "enable javascript" in html.lower()
            ):
                error = "Readmoo 疑似反機器人/JS 驗證，Actions 抓到的不是活動頁內容"

        blocked = False
        blocked_reason = ""

        if x["platform"] == "Readmoo":
            # 只要抓到的不是活動頁本體，就視為 blocked
            if error and ("robot" in error.lower() or "javascript" in error.lower() or "js" in error.lower()):
                blocked = True
                blocked_reason = "需要 JavaScript 驗證，Actions 無法取得活動清單"
        else:
            # 沒有 error 也可能拿到驗證頁（200 OK）
            h = (html or "").lower() if "html" in locals() else ""
            if "verify that you're not a robot" in h or "enable javascript" in h:
                blocked = True
                blocked_reason = "需要 JavaScript 驗證，Actions 無法取得活動清單"
            # 或是根本沒有 READMOO_CAMPAIGNS（你走 JS 變數抽取那條路時很有用）
            if (not blocked) and ("readmoo_campaigns" not in h):
            # 這條比較保守：只有當 card_titles 也空才判定
                if not card_titles:
                    blocked = True
                    blocked_reason = "疑似反機器人/JS 驗證，無法取得活動清單"

        if x.get("extra") is None and x["platform"] not in ("Kobo", "Pubu"):  
            cards = ["（未設定解析器 extra，暫時不會解析出活動）"]

        # 博客來：入口模式（不顯示擷取卡片，避免被商品/套組洗版）
        if x["platform"] == "博客來":
            blocked = True
            blocked_reason = "入口模式：博客來活動頁資訊流雜訊高，v1 先只保留入口連結"

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
                "blocked": blocked,
                "blocked_reason": blocked_reason,
            }
        )

    os.makedirs("data", exist_ok=True)

    payload = {
        "parser_version": PARSER_VERSION,
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

        is_blocked = bool(it.get("blocked"))

        # 模式 3：Readmoo / 博客來 若 blocked，就不顯示卡片區塊，只顯示原因＋連結
        if it["platform"] in ("Readmoo", "博客來") and is_blocked:
            reason = it.get("blocked_reason") or "入口模式"
            html_lines.append(f"<p style='margin:6px 0; color:#666;'>（{reason}）</p>")
        else:
            if it.get("card_titles"):
                html_lines.append("<div style='margin:8px 0 6px;'><b>活動卡片（擷取）</b></div>")
                html_lines.append("<ul style='margin:6px 0 10px 18px;'>")
                for t in it["card_titles"][:10]:
                    html_lines.append(f"<li>{t}</li>")
                html_lines.append("</ul>")

        # error：若是 Readmoo/博客來入口模式，就不要用紅字嚇人（原因已經顯示）
        if it.get("error") and not (it["platform"] in ("Readmoo", "博客來") and is_blocked):
            html_lines.append(
                f"<p style='margin:6px 0; color:#b00020;'>（抓取失敗：{it['error']}）</p>"
            )
       

        html_lines.append(f"<p style='margin:6px 0;'><a href='{it['url']}' target='_blank' rel='noopener noreferrer'>→ 點我查看活動</a></p>")



        html_lines.append("</section>")
        html_lines.append("<hr style='opacity:.25'/>")

    html_lines.append("<p style='font-size:12px;opacity:.7;margin-top:14px;'>v1 只彙整官方活動入口；不計券後價與單書特價。403 平台已自動弱化顯示。</p>")
    html_lines.append("</body></html>")

    with open(OUT_HTML, "w", encoding="utf-8") as f:
        f.write("\n".join(html_lines))


if __name__ == "__main__":
    main()
