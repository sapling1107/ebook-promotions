"""Build the v2 ebook promotion snapshot JSON and static HTML page."""

from __future__ import annotations

import html as html_module
import json
import re
import unicodedata
from collections import OrderedDict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urljoin, urlsplit, urlunsplit

import requests
from bs4 import BeautifulSoup, Tag


SCHEMA_VERSION = 2
PARSER_VERSION = 3
PROJECT_ROOT = Path(__file__).resolve().parents[1]
OUT_JSON = PROJECT_ROOT / "data" / "deals.json"
OUT_HTML = PROJECT_ROOT / "index.html"

PUBU_DAILY_99_URL = "https://www.pubu.com.tw/campaign/event/pubu99select"
REQUEST_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-TW,zh;q=0.9,en;q=0.6",
    "Cache-Control": "no-cache",
    "Pragma": "no-cache",
}

PLATFORMS: list[dict[str, Any]] = [
    {
        "platform": "Pubu",
        "url": "https://www.pubu.com.tw/activity/ongoing",
        "mode": "full",
        "note": "完整擷取本次活動頁回應中的全部卡片（不含每日 99 元專區）",
        "parser": "pubu",
        "extra_links": [
            {
                "label": "Pubu 每日 99 元專區",
                "url": PUBU_DAILY_99_URL,
                "note": "補充入口，不納入活動清單、新活動判定或活動數量",
            }
        ],
    },
    {
        "platform": "BookWalker",
        "url": "https://www.bookwalker.com.tw/event",
        "mode": "partial",
        "note": "部分擷取活動卡片；同一 href 合併為同一筆活動",
        "parser": "bookwalker",
        "extra_links": [],
    },
    {
        "platform": "Kobo",
        "url": "https://www.kobo.com/tw/zh/p/tw-publicationpicks-wkdsale",
        "mode": "partial",
        "note": "指定活動頁摘要，只擷取明確的活動標題連結",
        "parser": "kobo",
        "extra_links": [],
    },
    {
        "platform": "Readmoo",
        "url": "https://readmoo.com/campaign/activities",
        "mode": "entry",
        "note": "入口模式，不嘗試繞過 403 或反機器人機制",
        "parser": None,
        "extra_links": [
            {
                "label": "Readmoo 每日優惠",
                "url": "https://readmoo.com/campaign/specialoffer/index",
                "note": "補充入口，不納入活動清單、新活動判定或活動數量",
            }
        ],
    },
    {
        "platform": "HyRead",
        "url": "https://ebook.hyread.com.tw/Template/store/event_list.jsp",
        "mode": "entry",
        "note": "入口模式，不使用猜測式 parser",
        "parser": None,
        "extra_links": [],
    },
    {
        "platform": "博客來",
        "url": "https://activity.books.com.tw/crosscat/show/A00000062854?loc=mood_001",
        "mode": "entry",
        "note": "入口模式，不解析商品、榜單或預告",
        "parser": None,
        "extra_links": [],
    },
]


def clean_text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def normalize_title(value: str) -> str:
    return clean_text(unicodedata.normalize("NFKC", value)).casefold()


def normalize_url(value: str | None, base_url: str) -> str | None:
    if not value:
        return None

    absolute = urljoin(base_url, clean_text(value))
    parts = urlsplit(absolute)
    if parts.scheme.lower() not in {"http", "https"} or not parts.netloc:
        return None

    filtered_query = []
    for query_part in parts.query.split("&") if parts.query else []:
        key = query_part.split("=", 1)[0]
        lowered = key.casefold()
        if lowered.startswith("utm_") or lowered in {"fbclid", "gclid", "loc"}:
            continue
        filtered_query.append(query_part)

    path = parts.path or "/"
    if path != "/":
        path = path.rstrip("/")

    return urlunsplit(
        (
            parts.scheme.lower(),
            parts.netloc.lower(),
            path,
            "&".join(filtered_query),
            "",
        )
    )


def make_item(
    title: str,
    url: str | None,
    *,
    subtitle: str | None = None,
    period_text: str | None = None,
) -> dict[str, Any]:
    return {
        "title": clean_text(title),
        "subtitle": clean_text(subtitle) or None,
        "period_text": clean_text(period_text) or None,
        "url": url,
        "is_new": False,
    }


def extract_page_title(raw_html: str) -> str | None:
    soup = BeautifulSoup(raw_html, "html.parser")
    if not soup.title:
        return None
    return clean_text(soup.title.get_text(" ", strip=True)) or None


def fetch_html_requests(url: str) -> dict[str, Any]:
    response = requests.get(url, headers=REQUEST_HEADERS, timeout=25)
    status = response.status_code
    response.raise_for_status()
    response.encoding = response.apparent_encoding or "utf-8"
    return {"text": response.text, "status": status, "fetcher": "requests"}


def fetch_html_kobo_playwright(url: str) -> dict[str, Any]:
    """Kobo-only fallback. Playwright is intentionally imported lazily."""
    try:
        from playwright.sync_api import (
            TimeoutError as PlaywrightTimeoutError,
            sync_playwright,
        )
    except ImportError as exc:
        raise RuntimeError("Playwright fallback unavailable: package is not installed") from exc

    timeout_ms = 30_000
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent=REQUEST_HEADERS["User-Agent"],
            locale="zh-TW",
        )
        page = context.new_page()
        try:
            response = page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
            page.wait_for_selector("a.primary-heading", timeout=10_000)
            raw_html = page.content()
            status = response.status if response is not None else 200
        except PlaywrightTimeoutError as exc:
            raise RuntimeError(f"Kobo Playwright timeout after {timeout_ms}ms") from exc
        finally:
            context.close()
            browser.close()

    return {"text": raw_html, "status": status, "fetcher": "playwright"}


def extract_pubu_items(raw_html: str, base_url: str) -> dict[str, Any]:
    soup = BeautifulSoup(raw_html, "html.parser")
    cards = soup.select("main a.card-shadow[href], a.card-shadow[href]")
    daily_99_key = normalize_url(PUBU_DAILY_99_URL, base_url)
    items: list[dict[str, Any]] = []
    seen: set[str] = set()

    for card in cards:
        url = normalize_url(card.get("href"), base_url)
        if not url or url == daily_99_key or url in seen:
            continue

        heading = card.select_one("h1, h2, h3, h4")
        title = clean_text(heading.get_text(" ", strip=True) if heading else "")
        if not title:
            image = card.find("img", alt=True)
            title = clean_text(image.get("alt") if image else "")
        if not title:
            continue

        period_node = card.select_one(".time")
        period_text = clean_text(
            period_node.get_text(" ", strip=True) if period_node else ""
        )
        seen.add(url)
        items.append(make_item(title, url, period_text=period_text))

    return {"items": items, "found_container": bool(cards)}


def collect_tag_signals(tag: Tag) -> list[str]:
    # BookWalker commonly puts the main title in ``title`` and the subtitle in
    # ``alt``. Visible text is considered after those explicit signals because
    # malformed nesting can otherwise concatenate title and subtitle.
    signals = [tag.get("title"), tag.get("alt"), tag.get_text(" ", strip=True)]
    signals.extend(image.get("alt") for image in tag.select("img[alt]"))
    return [text for value in signals if (text := clean_text(value))]


def merge_unique_signals(target: list[str], signals: list[str]) -> None:
    normalized = {normalize_title(value) for value in target}
    for signal in signals:
        key = normalize_title(signal)
        if key and key not in normalized:
            target.append(signal)
            normalized.add(key)


_BOOKWALKER_EVERGREEN_PATH_TITLES = {
    "/pointshop/help": {"點數商店上線囉!"},
    "/selfapply": {"免費上架!流程便利!"},
    "/block/14": {"期間限定免費書籍"},
    "/block/13": {"附電子書獨家特別內容或贈品"},
    "/user/register": {"首次消費享結帳金額79折"},
    "/block/47": {"預購優惠活動"},
}
_BOOKWALKER_REPORT_RE = re.compile(
    r"(?P<year>20\d{2}).*(?:年度閱讀報告|b\s*☆\s*w\s*觀察報告)", re.IGNORECASE
)
_BOOKWALKER_DATE_TOKEN = (
    r"(?:20\d{2}[/-]\d{1,2}[/-]\d{1,2}|\d{1,2}[/.]\d{1,2})"
)
_BOOKWALKER_DATE_RANGE_RE = re.compile(
    rf"(?P<start>{_BOOKWALKER_DATE_TOKEN})\s*"
    rf"(?:至|到|[-~～—–－])\s*(?P<end>{_BOOKWALKER_DATE_TOKEN})"
)
_BOOKWALKER_PREFIX_END_RE = re.compile(
    rf"(?:至|到|截止(?:至)?)\s*(?P<end>{_BOOKWALKER_DATE_TOKEN})(?:\s*止)?"
)
_BOOKWALKER_SUFFIX_END_RE = re.compile(
    rf"(?P<end>{_BOOKWALKER_DATE_TOKEN})\s*(?:前|止|截止)"
)


def _bookwalker_url_parts(url: str) -> tuple[str, str]:
    parts = urlsplit(url)
    path = parts.path.rstrip("/") or "/"
    return parts.netloc.casefold(), path.casefold()


def is_bookwalker_evergreen_entry(title: str, url: str) -> bool:
    normalized_title = normalize_title(title)
    hostname, path = _bookwalker_url_parts(url)

    expected_titles = _BOOKWALKER_EVERGREEN_PATH_TITLES.get(path)
    if (
        hostname in {"bookwalker.com.tw", "www.bookwalker.com.tw"}
        and expected_titles
        and normalized_title in expected_titles
    ):
        return True
    if hostname == "lin.ee" and normalized_title == "line好友限定9折優惠券":
        return True
    if (
        hostname == "cp.bookwalker.com.tw"
        and path == "/event/2023/20230407"
        and normalized_title == "快來追蹤這些社群"
    ):
        return True
    return (
        hostname in {"bookwalker.com.tw", "www.bookwalker.com.tw"}
        and path == "/search"
        and normalized_title == "動畫化書籍一覽"
    )


def is_bookwalker_historical_report(title: str, today: date) -> bool:
    match = _BOOKWALKER_REPORT_RE.search(normalize_title(title))
    return bool(match and int(match.group("year")) < today.year)


def _parse_bookwalker_date_token(token: str) -> tuple[int | None, int, int] | None:
    values = [int(value) for value in re.split(r"[./-]", token)]
    if len(values) == 3:
        return values[0], values[1], values[2]
    if len(values) == 2:
        return None, values[0], values[1]
    return None


def _make_bookwalker_date(year: int, month: int, day: int) -> date | None:
    try:
        return date(year, month, day)
    except ValueError:
        return None


def _bookwalker_range_end(start_token: str, end_token: str, today: date) -> date | None:
    start_parts = _parse_bookwalker_date_token(start_token)
    end_parts = _parse_bookwalker_date_token(end_token)
    if not start_parts or not end_parts:
        return None

    start_year, start_month, start_day = start_parts
    end_year, end_month, end_day = end_parts
    if end_year is not None:
        return _make_bookwalker_date(end_year, end_month, end_day)

    if start_year is not None:
        inferred_year = start_year + (
            (end_month, end_day) < (start_month, start_day)
        )
        return _make_bookwalker_date(inferred_year, end_month, end_day)

    intervals: list[tuple[int, date]] = []
    for inferred_start_year in range(today.year - 1, today.year + 2):
        inferred_end_year = inferred_start_year + (
            (end_month, end_day) < (start_month, start_day)
        )
        start_date = _make_bookwalker_date(
            inferred_start_year, start_month, start_day
        )
        end_date = _make_bookwalker_date(inferred_end_year, end_month, end_day)
        if not start_date or not end_date:
            continue
        if start_date <= today <= end_date:
            distance = 0
        else:
            distance = min(
                abs((today - start_date).days), abs((today - end_date).days)
            )
        intervals.append((distance, end_date))

    return min(intervals, key=lambda value: value[0])[1] if intervals else None


def _bookwalker_deadline(token: str, today: date) -> date | None:
    parts = _parse_bookwalker_date_token(token)
    if not parts:
        return None
    year, month, day = parts
    if year is not None:
        return _make_bookwalker_date(year, month, day)

    candidates = [
        candidate
        for candidate_year in range(today.year - 1, today.year + 2)
        if (candidate := _make_bookwalker_date(candidate_year, month, day))
    ]
    return min(candidates, key=lambda value: abs((value - today).days), default=None)


def extract_bookwalker_end_date(
    title: str, subtitle: str | None, today: date
) -> date | None:
    text = " ".join(value for value in (title, subtitle) if value)
    candidates: list[date] = []

    for match in _BOOKWALKER_DATE_RANGE_RE.finditer(text):
        end_date = _bookwalker_range_end(
            match.group("start"), match.group("end"), today
        )
        if end_date:
            candidates.append(end_date)

    for pattern in (_BOOKWALKER_PREFIX_END_RE, _BOOKWALKER_SUFFIX_END_RE):
        for match in pattern.finditer(text):
            end_date = _bookwalker_deadline(match.group("end"), today)
            if end_date:
                candidates.append(end_date)

    # If multiple explicit deadlines are present, use the latest one. This is
    # deliberately conservative and avoids dropping an activity too early.
    return max(candidates, default=None)


def should_exclude_bookwalker_item(
    title: str, subtitle: str | None, url: str, today: date
) -> bool:
    if is_bookwalker_evergreen_entry(title, url):
        return True
    if is_bookwalker_historical_report(title, today):
        return True
    end_date = extract_bookwalker_end_date(title, subtitle, today)
    return bool(end_date and end_date < today - timedelta(days=1))


def extract_bookwalker_items(raw_html: str, base_url: str) -> dict[str, Any]:
    soup = BeautifulSoup(raw_html, "html.parser")
    cards = soup.select(".listbox_bwmain .banner_package")
    grouped: OrderedDict[str, list[str]] = OrderedDict()

    for card in cards:
        card_groups: OrderedDict[str, list[str]] = OrderedDict()
        for anchor in card.select("a[href]"):
            url = normalize_url(anchor.get("href"), base_url)
            if not url:
                continue
            signals = collect_tag_signals(anchor)
            if url not in card_groups:
                card_groups[url] = []
            merge_unique_signals(card_groups[url], signals)

        for url, signals in card_groups.items():
            if not signals:
                continue
            if url not in grouped:
                grouped[url] = []
            merge_unique_signals(grouped[url], signals)

    items: list[dict[str, Any]] = []
    today = datetime.now(timezone(timedelta(hours=8))).date()
    for url, signals in grouped.items():
        if not signals:
            continue
        title = signals[0]
        subtitle = next(
            (
                value
                for value in signals[1:]
                if normalize_title(value) != normalize_title(title)
                and normalize_title(value) not in normalize_title(title)
                and normalize_title(title) not in normalize_title(value)
            ),
            None,
        )
        if should_exclude_bookwalker_item(title, subtitle, url, today):
            continue
        items.append(make_item(title, url, subtitle=subtitle))

    return {"items": items, "found_container": bool(cards)}


def extract_kobo_items(raw_html: str, base_url: str) -> dict[str, Any]:
    soup = BeautifulSoup(raw_html, "html.parser")
    headings = soup.select("a.primary-heading[href]")
    items: list[dict[str, Any]] = []
    seen: set[str] = set()

    for heading in headings:
        title = clean_text(heading.get_text(" ", strip=True))
        url = normalize_url(heading.get("href"), base_url)
        if not title or not url or url in seen:
            continue
        seen.add(url)
        items.append(make_item(title, url))

    return {"items": items, "found_container": bool(headings)}


PARSERS: dict[str, Callable[[str, str], dict[str, Any]]] = {
    "pubu": extract_pubu_items,
    "bookwalker": extract_bookwalker_items,
    "kobo": extract_kobo_items,
}


def entry_platform_result(config: dict[str, Any]) -> dict[str, Any]:
    return {
        "platform": config["platform"],
        "url": config["url"],
        "mode": config["mode"],
        "note": config["note"],
        "page_title": None,
        "status": "entry_only",
        "http_status": None,
        "error": None,
        "items": [],
        "extra_links": config["extra_links"],
    }


def status_from_parse(parse_result: dict[str, Any]) -> str:
    if parse_result["items"]:
        return "ok"
    if parse_result["found_container"]:
        return "empty"
    return "parse_error"


def get_http_status(exc: Exception) -> int | None:
    if isinstance(exc, requests.HTTPError) and exc.response is not None:
        return exc.response.status_code
    return None


def short_error(exc: Exception) -> str:
    return clean_text(str(exc))[:500] or exc.__class__.__name__


def scraped_platform_result(config: dict[str, Any]) -> dict[str, Any]:
    parser = PARSERS[config["parser"]]
    raw_html = ""
    http_status: int | None = None
    request_error: Exception | None = None

    try:
        response = fetch_html_requests(config["url"])
        raw_html = response["text"]
        http_status = response["status"]
        parse_result = parser(raw_html, config["url"])
    except Exception as exc:
        request_error = exc
        http_status = get_http_status(exc)
        parse_result = {"items": [], "found_container": False}

    # Kobo alone may use the existing Playwright scope as a fallback. The fallback
    # is used only when requests failed or yielded no structured activity items.
    if config["platform"] == "Kobo" and not parse_result["items"]:
        try:
            response = fetch_html_kobo_playwright(config["url"])
            raw_html = response["text"]
            http_status = response["status"]
            parse_result = parser(raw_html, config["url"])
            request_error = None
        except Exception as fallback_error:
            errors = []
            if request_error is not None:
                errors.append(f"requests: {short_error(request_error)}")
            errors.append(f"Playwright fallback: {short_error(fallback_error)}")
            return {
                "platform": config["platform"],
                "url": config["url"],
                "mode": config["mode"],
                "note": config["note"],
                "page_title": extract_page_title(raw_html) if raw_html else None,
                "status": "http_error" if http_status else "fetch_error",
                "http_status": http_status,
                "error": "; ".join(errors),
                "items": [],
                "extra_links": config["extra_links"],
            }

    if request_error is not None:
        return {
            "platform": config["platform"],
            "url": config["url"],
            "mode": config["mode"],
            "note": config["note"],
            "page_title": None,
            "status": "http_error" if http_status else "fetch_error",
            "http_status": http_status,
            "error": short_error(request_error),
            "items": [],
            "extra_links": config["extra_links"],
        }

    status = status_from_parse(parse_result)
    error = None
    if status == "parse_error":
        error = "HTTP 回應成功，但找不到預期的活動卡片結構"

    return {
        "platform": config["platform"],
        "url": config["url"],
        "mode": config["mode"],
        "note": config["note"],
        "page_title": extract_page_title(raw_html),
        "status": status,
        "http_status": http_status,
        "error": error,
        "items": parse_result["items"],
        "extra_links": config["extra_links"],
    }


def load_previous_payload() -> dict[str, Any] | None:
    try:
        with OUT_JSON.open("r", encoding="utf-8") as file:
            data = json.load(file)
        return data if isinstance(data, dict) else None
    except (OSError, json.JSONDecodeError):
        return None


def item_identity(item: dict[str, Any], platform_url: str) -> str | None:
    item_url = normalize_url(item.get("url"), platform_url)
    source_url = normalize_url(platform_url, platform_url)
    if item_url and item_url != source_url:
        return f"url:{item_url}"
    title = normalize_title(item.get("title") or "")
    return f"title:{title}" if title else None


def mark_new_items(
    platforms: list[dict[str, Any]], previous: dict[str, Any] | None
) -> list[str]:
    for platform in platforms:
        for item in platform.get("items", []):
            item["is_new"] = False

    # A v1 file has no reliable activity URLs, so the first v2 run deliberately
    # establishes a baseline without marking any existing activity as new.
    if not previous or previous.get("schema_version") != SCHEMA_VERSION:
        return []

    previous_by_platform = {
        value.get("platform"): value
        for value in previous.get("platforms", [])
        if isinstance(value, dict) and value.get("platform")
    }
    new_platforms: list[str] = []

    for platform in platforms:
        previous_platform = previous_by_platform.get(platform["platform"])
        if (
            platform["status"] != "ok"
            or not previous_platform
            or previous_platform.get("status") != "ok"
        ):
            continue

        previous_keys = {
            key
            for item in previous_platform.get("items", [])
            if isinstance(item, dict)
            if (key := item_identity(item, previous_platform.get("url") or platform["url"]))
        }
        for item in platform["items"]:
            key = item_identity(item, platform["url"])
            item["is_new"] = bool(key and key not in previous_keys)

        if any(item["is_new"] for item in platform["items"]):
            new_platforms.append(platform["platform"])

    return new_platforms


def escape_text(value: Any) -> str:
    return html_module.escape(str(value or ""), quote=True)


def render_header() -> str:
    return """<!doctype html>
<html lang="zh-Hant">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>電子書平台活動快照</title>
<style>
:root { color-scheme: light; }
body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Noto Sans TC", Helvetica, Arial, sans-serif; line-height: 1.65; padding: 16px; max-width: 980px; margin: 0 auto; color: #111; }
h1 { margin: 0 0 8px; }
h2 { margin: 6px 0; }
p { margin: 4px 0; }
.summary { margin-bottom: 16px; }
.notice { font-size: .85rem; color: #666; margin: 8px 0 10px; }
.platform { padding: 12px 0; }
.meta, .status, .item-detail, .extra-note { color: #666; }
.status-error { color: #b00020; }
.items { margin: 8px 0 10px 20px; padding: 0; }
.items li { margin: 7px 0; }
.new-badge { margin-right: .3em; }
.extra-links { margin: 8px 0; padding: 10px 14px; background: #f6f7f8; border-radius: 8px; }
hr { border: 0; border-top: 1px solid #ddd; }
footer { font-size: .8rem; color: #666; margin-top: 14px; }
</style>
</head>
<body>"""


def render_summary(payload: dict[str, Any]) -> str:
    has_new = payload["has_new_items"]
    new_platforms = payload["new_platforms"]
    updated_at_text = datetime.fromisoformat(payload["updated_at"]).strftime(
        "%Y/%m/%d %H:%M"
    )
    platform_text = "、".join(escape_text(value) for value in new_platforms)
    platform_suffix = f"（新增活動平台：{platform_text}）" if platform_text else ""
    return (
        "<header>"
        "<h1>📚 電子書平台活動快照</h1>"
        f"<p>更新時間（台灣）：<strong>{escape_text(updated_at_text)}</strong></p>"
        f"<p class=\"summary\">今天是否有新增活動：<strong>{'是' if has_new else '否'}</strong>"
        f"{platform_suffix}</p>"
        "<p class=\"notice\">※ 本頁僅彙整活動標題與官方入口；不保證資訊完整性、即時性或實際優惠內容，請以各平台官方說明為準。</p>"
        "</header><hr>"
    )


def render_status(platform: dict[str, Any]) -> str:
    status = platform["status"]
    if status == "entry_only":
        return '<p class="status">入口模式：僅提供官方頁面連結。</p>'
    if status == "ok":
        return f'<p class="status">活動數量：{len(platform["items"])}</p>'
    if status == "empty":
        return '<p class="status">本次未找到可顯示的活動。</p>'

    error = escape_text(platform.get("error") or "未知錯誤")
    http_status = platform.get("http_status")
    http_text = f"（HTTP {http_status}）" if http_status is not None else ""
    return f'<p class="status status-error">擷取失敗{http_text}：{error}</p>'


def render_items(items: list[dict[str, Any]]) -> str:
    if not items:
        return ""

    lines = ['<div class="item-block"><strong>活動清單</strong><ul class="items">']
    for item in items:
        badge = '<span class="new-badge" aria-label="新活動">🆕</span>' if item["is_new"] else ""
        title = escape_text(item["title"])
        if item.get("url"):
            title_html = (
                f'<a href="{escape_text(item["url"])}" target="_blank" '
                f'rel="noopener noreferrer">{title}</a>'
            )
        else:
            title_html = title

        details = [item.get("subtitle"), item.get("period_text")]
        detail_html = "".join(
            f'<div class="item-detail">{escape_text(value)}</div>'
            for value in details
            if value
        )
        lines.append(f"<li>{badge}{title_html}{detail_html}</li>")
    lines.append("</ul></div>")
    return "\n".join(lines)


def render_extra_links(extra_links: list[dict[str, Any]]) -> str:
    if not extra_links:
        return ""

    lines = ['<div class="extra-links"><strong>補充入口</strong><ul>']
    for link in extra_links:
        note = (
            f'<div class="extra-note">{escape_text(link["note"])}</div>'
            if link.get("note")
            else ""
        )
        lines.append(
            f'<li><a href="{escape_text(link["url"])}" target="_blank" '
            f'rel="noopener noreferrer">{escape_text(link["label"])}</a>{note}</li>'
        )
    lines.append("</ul></div>")
    return "\n".join(lines)


def render_platform_section(platform: dict[str, Any]) -> str:
    mode_labels = {"full": "完整擷取", "partial": "部分擷取", "entry": "入口模式"}
    page_title = (
        f'<p class="meta">頁面標題：{escape_text(platform["page_title"])}</p>'
        if platform.get("page_title")
        else ""
    )
    source_link = (
        f'<p><a href="{escape_text(platform["url"])}" target="_blank" '
        'rel="noopener noreferrer">→ 前往官方活動頁</a></p>'
    )
    return "\n".join(
        [
            '<section class="platform">',
            f'<h2>{escape_text(platform["platform"])}</h2>',
            f'<p class="meta">模式：{escape_text(mode_labels[platform["mode"]])}</p>',
            f'<p class="meta">備註：{escape_text(platform["note"])}</p>',
            page_title,
            render_status(platform),
            render_items(platform["items"]),
            source_link,
            render_extra_links(platform["extra_links"]),
            "</section><hr>",
        ]
    )


def render_footer() -> str:
    return (
        "<footer>v2 僅整理指定官方活動頁及入口；不計券後價、單書比價或推薦。"
        "補充入口不列入活動數量及新增判定。</footer></body></html>"
    )


def render_document(payload: dict[str, Any]) -> str:
    sections = "\n".join(render_platform_section(value) for value in payload["platforms"])
    return "\n".join(
        [render_header(), render_summary(payload), "<main>", sections, "</main>", render_footer()]
    )


def build_payload(previous: dict[str, Any] | None) -> dict[str, Any]:
    platforms = [
        entry_platform_result(config)
        if config["mode"] == "entry"
        else scraped_platform_result(config)
        for config in PLATFORMS
    ]
    new_platforms = mark_new_items(platforms, previous)
    now = datetime.now(timezone(timedelta(hours=8))).isoformat(timespec="minutes")
    return {
        "schema_version": SCHEMA_VERSION,
        "parser_version": PARSER_VERSION,
        "updated_at": now,
        "has_new_items": bool(new_platforms),
        "new_platforms": new_platforms,
        "platforms": platforms,
    }


def main() -> None:
    previous = load_previous_payload()
    payload = build_payload(previous)

    OUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    OUT_JSON.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    OUT_HTML.write_text(render_document(payload) + "\n", encoding="utf-8")

    for platform in payload["platforms"]:
        print(
            f"[{platform['platform']}] mode={platform['mode']} "
            f"status={platform['status']} items={len(platform['items'])}"
        )


if __name__ == "__main__":
    main()
