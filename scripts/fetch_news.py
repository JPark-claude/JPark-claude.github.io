"""관심 종목 뉴스를 모아 news.json으로 저장한다.

GitHub Actions가 30분마다 실행한다 (.github/workflows/news.yml).
출처: Yahoo Finance RSS, Google News RSS(영문·국문, Reuters·CNBC·Investing.com 등
여러 매체 기사가 들어옴), Nasdaq RSS.
표준 라이브러리만 사용한다.
"""
import gzip
import json
import re
import sys
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone, timedelta
from email.utils import parsedate_to_datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
UA_BROWSER = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
UA_SEC = "MarketDesk JPark-claude@users.noreply.github.com"
MAX_PER_TICKER = 60
MAX_AGE_DAYS = 21
FOREIGN_FILERS = {"STM", "ASML"}  # 20-F/6-K 제출 기업


def get(url, ua=UA_BROWSER, timeout=20):
    headers = {"User-Agent": ua, "Accept": "application/rss+xml, application/atom+xml, application/xml, text/xml, */*", "Accept-Encoding": "gzip, deflate"}
    if "sec.gov" in url:
        headers["Host"] = "www.sec.gov"
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        data = r.read()
        if r.headers.get("Content-Encoding") == "gzip":
            data = gzip.decompress(data)
        return data


def to_iso(text):
    if not text:
        return None
    try:
        d = parsedate_to_datetime(text)
    except (TypeError, ValueError):
        try:
            d = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            return None
    if d.tzinfo is None:
        d = d.replace(tzinfo=timezone.utc)
    return d.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def clean(s):
    s = re.sub(r"<[^>]+>", "", s or "")
    return re.sub(r"\s+", " ", s).strip()


def parse_rss(raw, via, default_source=None, strip_source_suffix=False):
    out = []
    root = ET.fromstring(raw)
    for it in root.iter("item"):
        title = clean(it.findtext("title"))
        link = (it.findtext("link") or "").strip()
        src_el = it.find("source")
        source = clean(src_el.text) if src_el is not None and src_el.text else default_source
        if strip_source_suffix and source and title.endswith(" - " + source):
            title = title[: -len(" - " + source)].strip()
        if not title or not link.startswith(("http://", "https://")):
            continue
        out.append({"title": title, "url": link, "source": source or via, "time": to_iso(it.findtext("pubDate")), "via": via})
    return out


def parse_sec_atom(raw):
    ns = {"a": "http://www.w3.org/2005/Atom"}
    out = []
    root = ET.fromstring(raw)
    for e in root.findall("a:entry", ns):
        title = clean(e.findtext("a:title", default="", namespaces=ns))
        link_el = e.find("a:link", ns)
        link = link_el.get("href") if link_el is not None else ""
        summary = clean(e.findtext("a:summary", default="", namespaces=ns))
        items = re.findall(r"Item\s+\d+\.\d+[^I]*", summary)
        if items:
            title = title + " · " + "; ".join(i.strip()[:60] for i in items[:2])
        if title and link.startswith("https://"):
            out.append({"title": title, "url": link, "source": "SEC 공시", "time": to_iso(e.findtext("a:updated", default="", namespaces=ns)), "via": "SEC"})
    return out


def gnews(query, lang):
    if lang == "ko":
        params = {"q": query, "hl": "ko", "gl": "KR", "ceid": "KR:ko"}
    else:
        params = {"q": query, "hl": "en-US", "gl": "US", "ceid": "US:en"}
    return "https://news.google.com/rss/search?" + urllib.parse.urlencode(params)


def collect(tk):
    t = tk["t"]
    foreign = ":" in t
    yahoo = tk.get("yahoo") or t
    en = tk.get("en") or t
    ko = tk.get("ko") or en
    jobs = [
        ("en", lambda: parse_rss(get("https://feeds.finance.yahoo.com/rss/2.0/headline?" + urllib.parse.urlencode({"s": yahoo, "region": "US", "lang": "en-US"})), "Yahoo Finance", "Yahoo Finance")),
        ("en", lambda: parse_rss(get(gnews('"%s" when:14d' % en, "en")), "Google News", strip_source_suffix=True)),
        ("ko", lambda: parse_rss(get(gnews("%s 주가 when:14d" % ko, "ko")), "Google News", strip_source_suffix=True)),
    ]
    if not foreign:
        jobs.append(("en", lambda: parse_rss(get("https://www.nasdaq.com/feed/rssoutbound?symbol=" + urllib.parse.quote(t)), "Nasdaq", "Nasdaq")))
        # SEC 8-K 피드는 GitHub 서버에서 접속을 막아(403) 수집하지 않는다. 공시는 공시 탭 링크로 본다.

    items, errors = [], []
    for lang, job in jobs:
        try:
            for x in job():
                x["lang"] = lang
                items.append(x)
        except Exception as ex:  # 한 출처가 실패해도 나머지는 계속
            errors.append("%s %s: %s" % (getattr(job, "label", ""), type(ex).__name__, str(ex)[:80]))
        time.sleep(0.4)

    cutoff = datetime.now(timezone.utc) - timedelta(days=MAX_AGE_DAYS)
    seen, kept = set(), []
    for x in sorted(items, key=lambda i: i["time"] or "", reverse=True):
        key = re.sub(r"[^0-9a-z가-힣]", "", x["title"].lower())[:70]
        if key in seen:
            continue
        if x["time"] and datetime.fromisoformat(x["time"].replace("Z", "+00:00")) < cutoff:
            continue
        seen.add(key)
        kept.append(x)
    sec = [x for x in kept if x["via"] == "SEC"][:5]  # 공시는 개수 제한에 밀려나지 않게
    rest = [x for x in kept if x["via"] != "SEC"][: MAX_PER_TICKER - len(sec)]
    merged = sorted(sec + rest, key=lambda i: i["time"] or "", reverse=True)
    return merged, errors


def main():
    tickers = json.loads((ROOT / "tickers.json").read_text(encoding="utf-8"))
    out_path = ROOT / "news.json"
    old = {}
    if out_path.exists():
        try:
            old = json.loads(out_path.read_text(encoding="utf-8")).get("tickers", {})
        except ValueError:
            old = {}
    result = {}
    for tk in tickers:
        items, errors = collect(tk)
        if not items and tk["t"] in old:  # 전부 실패하면 이전 결과 유지
            result[tk["t"]] = dict(old[tk["t"]], stale=True)
        else:
            result[tk["t"]] = {"name": tk.get("ko"), "items": items}
        print(tk["t"], len(items), "items", ("errors: " + " | ".join(errors)) if errors else "", file=sys.stderr)
    payload = {"updated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "tickers": result}
    out_path.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")


if __name__ == "__main__":
    main()
