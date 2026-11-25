"""미국장 마감 후 모닝 브리핑 자료를 모아 briefing.json으로 저장한다.

GitHub Actions가 화~토 오전 7시 30분(KST)에 실행한다 (.github/workflows/briefing.yml).
- 주요 지수·금리·환율·원자재 종가와 등락률 (Yahoo Finance 차트 API)
- 관심 종목(tickers.json) 등락률
- 증시 마감 헤드라인과 AI·테크 헤드라인 (Google News RSS, Yahoo Finance RSS)
이 자료를 바탕으로 Claude 예약 작업이 한국어 요약(briefing-ai.json)을 덧붙인다.
"""
import json
import sys
import time
import urllib.parse
from datetime import datetime, timezone, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from fetch_news import get, parse_rss, gnews  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
KST = timezone(timedelta(hours=9))

MARKETS = [
    ("^GSPC", "S&P 500", "index"),
    ("^IXIC", "나스닥", "index"),
    ("^DJI", "다우", "index"),
    ("^SOX", "필라델피아 반도체", "index"),
    ("^RUT", "러셀 2000", "index"),
    ("^VIX", "VIX", "vol"),
    ("^TNX", "미 10년물 금리", "yield"),
    ("DX-Y.NYB", "달러 인덱스", "fx"),
    ("KRW=X", "원/달러", "fx"),
    ("JPY=X", "엔/달러", "fx"),
    ("CL=F", "WTI 유가", "cmdty"),
    ("GC=F", "금", "cmdty"),
    ("BTC-USD", "비트코인", "crypto"),
]

TRUSTED = ["Reuters", "Bloomberg", "CNBC", "MarketWatch", "Investing.com", "Yahoo Finance", "Barron's",
           "The Wall Street Journal", "WSJ", "Financial Times", "Associated Press", "AP News", "Nasdaq",
           "The Verge", "TechCrunch", "Axios", "The Information", "Wired", "Ars Technica", "Fortune",
           "Business Insider", "The New York Times", "Nikkei Asia", "Tom's Hardware", "연합뉴스",
           "연합인포맥스", "한국경제", "매일경제", "조선비즈", "서울경제", "머니투데이", "이데일리", "전자신문"]


def chart(symbol):
    url = "https://query1.finance.yahoo.com/v8/finance/chart/%s?range=7d&interval=1d" % urllib.parse.quote(symbol)
    d = json.loads(get(url))
    r = d["chart"]["result"][0]
    ts = r.get("timestamp") or []
    closes = r["indicators"]["quote"][0].get("close") or []
    pts = [(t, c) for t, c in zip(ts, closes) if c is not None]
    if len(pts) < 2:
        raise ValueError("not enough points")
    (t0, prev), (t1, last) = pts[-2], pts[-1]
    return {
        "close": round(last, 4),
        "prev": round(prev, 4),
        "chg": round(last - prev, 4),
        "pct": round((last / prev - 1) * 100, 2) if prev else None,
        "date": datetime.fromtimestamp(t1, tz=timezone.utc).astimezone(timezone(timedelta(hours=-5))).strftime("%Y-%m-%d"),
    }


def is_trusted(src):
    return any(src == t or (src or "").startswith(t) for t in TRUSTED)


def headlines(queries, limit):
    items = []
    for kind, q in queries:
        try:
            if kind == "yahoo":
                items += parse_rss(get(q), "Yahoo Finance", "Yahoo Finance")
            else:
                lang = "ko" if kind == "ko" else "en"
                for x in parse_rss(get(gnews(q, lang)), "Google News", strip_source_suffix=True):
                    x["lang"] = lang
                    items.append(x)
        except Exception as ex:
            print("headline error", q[:50], ex, file=sys.stderr)
        time.sleep(0.4)
    cutoff = datetime.now(timezone.utc) - timedelta(hours=30)
    seen, out = set(), []
    for x in sorted(items, key=lambda i: (is_trusted(i["source"]), i["time"] or ""), reverse=True):
        key = "".join(ch for ch in x["title"].lower() if ch.isalnum())[:60]
        if key in seen:
            continue
        if x["time"] and datetime.fromisoformat(x["time"].replace("Z", "+00:00")) < cutoff:
            continue
        seen.add(key)
        x.setdefault("lang", "en")
        x["trusted"] = is_trusted(x["source"])
        out.append(x)
    return out[:limit]


def main():
    markets, movers = [], []
    for sym, name, kind in MARKETS:
        try:
            c = chart(sym)
            c.update({"symbol": sym, "name": name, "kind": kind})
            markets.append(c)
        except Exception as ex:
            print("market error", sym, ex, file=sys.stderr)
        time.sleep(0.3)

    for tk in json.loads((ROOT / "tickers.json").read_text(encoding="utf-8")):
        sym = tk.get("yahoo") or tk["t"]
        try:
            c = chart(sym)
            c.update({"t": tk["t"], "name": tk.get("ko") or tk["t"]})
            movers.append(c)
        except Exception as ex:
            print("ticker error", sym, ex, file=sys.stderr)
        time.sleep(0.3)

    market_news = headlines([
        ("en", "stock market today when:1d"),
        ("en", "Wall Street close stocks when:1d"),
        ("yahoo", "https://finance.yahoo.com/news/rssindex"),
        ("ko", "뉴욕증시 마감 when:1d"),
    ], 14)
    ai_news = headlines([
        ("en", "artificial intelligence OR OpenAI OR Anthropic OR Nvidia when:1d"),
        ("en", "semiconductor OR chipmaker OR data center AI when:1d"),
        ("en", "Big Tech AI Microsoft OR Google OR Meta OR Amazon OR Apple when:1d"),
        ("ko", "AI 반도체 when:1d"),
    ], 16)

    now = datetime.now(timezone.utc)
    us_date = max((m["date"] for m in markets), default=None)
    payload = {
        "generated": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "kst_date": now.astimezone(KST).strftime("%Y-%m-%d"),
        "us_session": us_date,
        "markets": markets,
        "movers": sorted(movers, key=lambda m: m["pct"] if m["pct"] is not None else 0, reverse=True),
        "market_news": market_news,
        "ai_news": ai_news,
    }
    (ROOT / "briefing.json").write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print("markets", len(markets), "movers", len(movers), "market_news", len(market_news), "ai_news", len(ai_news), file=sys.stderr)


if __name__ == "__main__":
    main()
