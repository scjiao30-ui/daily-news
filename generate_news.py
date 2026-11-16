import os
import json
import datetime
import urllib.request
import urllib.parse
import xml.etree.ElementTree as ET
import re
from concurrent.futures import ThreadPoolExecutor
from email.utils import parsedate_to_datetime
from html import escape, unescape
from html.parser import HTMLParser

TAIPEI = datetime.timezone(datetime.timedelta(hours=8))
NEWS_WINDOW = datetime.timedelta(hours=48)
MEDIA_RSS = "{http://search.yahoo.com/mrss/}"


def image_url(value, base):
    if not value:
        return ""
    url = urllib.parse.urljoin(base, value.strip())
    parsed = urllib.parse.urlsplit(url)
    return url if parsed.scheme in {"http", "https"} and parsed.hostname else ""


def rss_image(item, link):
    for name in ("content", "thumbnail"):
        for media in item.iter(f"{MEDIA_RSS}{name}"):
            if media.get("medium") not in (None, "image"):
                continue
            if media.get("type") and not media.get("type").startswith("image/"):
                continue
            url = image_url(media.get("url"), link)
            if url:
                return url
    for enclosure in item.findall("enclosure"):
        if enclosure.get("type", "").startswith("image/"):
            url = image_url(enclosure.get("url"), link)
            if url:
                return url
    return ""


def fetch_rss(url, now, max_items, source_name=None):
    items = []
    try:
        request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(request, timeout=12) as response:
            root = ET.fromstring(response.read())
        for entry in root.findall("./channel/item"):
            try:
                published = parsedate_to_datetime(entry.findtext("pubDate", ""))
                if published.tzinfo is None:
                    continue
            except (ValueError, TypeError, OverflowError):
                continue
            if not now - NEWS_WINDOW <= published <= now:
                continue
            title = entry.findtext("title", "").strip()
            link = image_url(entry.findtext("link", ""), url)
            if not title or not link:
                continue
            description = entry.findtext("description", "")
            description = unescape(re.sub(r"<[^>]+>", " ", description))
            description = " ".join(description.split())
            source = source_name or entry.findtext("source", "").strip() or "新聞來源"
            if not source_name:
                suffix = f" - {source}"
                if title.endswith(suffix):
                    title = title[:-len(suffix)]
            items.append({
                "title": title,
                "content": description[:160] + ("…" if len(description) > 160 else ""),
                "source_name": source,
                "source_url": link,
                "image_url": rss_image(entry, link),
                "published_at": published.isoformat(),
            })
    except (OSError, ValueError, ET.ParseError) as error:
        print(f"RSS fetch warning for {url}: {error}")
    return select_news(items, max_items)


def select_news(items, max_items):
    ordered = sorted(
        items, key=lambda item: datetime.datetime.fromisoformat(item["published_at"]),
        reverse=True,
    )
    selected = []
    seen_titles = set()
    seen_links = set()
    for item in ordered:
        if item["title"] in seen_titles or item["source_url"] in seen_links:
            continue
        seen_titles.add(item["title"])
        seen_links.add(item["source_url"])
        selected.append(item)
        if len(selected) == max_items:
            break
    return selected


def fetch_google_news_rss(query, now, max_items=5):
    encoded_q = urllib.parse.quote(f"{query} when:2d")
    url = f"https://news.google.com/rss/search?q={encoded_q}&hl=zh-TW&gl=TW&ceid=TW:zh-Hant"
    return fetch_rss(url, now, max_items)


def fetch_yahoo_news_with_images(url, now, max_items=4):
    return fetch_rss(url, now, max_items, source_name="Yahoo奇摩財經")


class PreviewImageParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.images = {}

    def handle_starttag(self, tag, attrs):
        if tag == "meta":
            attributes = dict(attrs)
            name = (attributes.get("property") or attributes.get("name") or "").lower()
            if name in {"og:image", "og:image:secure_url", "twitter:image", "twitter:image:src"}:
                self.images.setdefault(name, attributes.get("content"))


def add_preview_image(item):
    if item["image_url"]:
        return
    try:
        request = urllib.request.Request(item["source_url"], headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(request, timeout=5) as response:
            if response.headers.get_content_type() != "text/html":
                return
            page_url = response.geturl()
            html = response.read(192 * 1024).decode(
                response.headers.get_content_charset() or "utf-8", errors="replace"
            )
        parser = PreviewImageParser()
        parser.feed(html)
        for name in ("og:image:secure_url", "og:image", "twitter:image", "twitter:image:src"):
            url = image_url(parser.images.get(name), page_url)
            if url and urllib.parse.urlsplit(page_url).hostname != "news.google.com":
                item["image_url"] = url
                return
    except (OSError, ValueError, LookupError):
        pass


# 抓取台灣證交所最新收盤價，自動結合個股最新新聞
def fetch_focus_stocks(now):
    twse_map = {}
    try:
        req = urllib.request.Request(
            "https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL",
            headers={"User-Agent": "Mozilla/5.0"}
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            for row in json.loads(resp.read().decode("utf-8")):
                code = row.get("Code", "").strip()
                if len(code) == 4 and code.isdigit():
                    twse_map[code] = {
                        "name": row.get("Name", "").strip(),
                        "close": row.get("ClosingPrice", "-"),
                        "change": row.get("Change", "0"),
                        "val": float(row.get("TradeValue", "0").replace(",", "") or 0)
                    }
    except Exception as e:
        print(f"TWSE fetch warning: {e}")

    # 預設核心權值股 + 自動挑出當日成交值最高的前幾名熱門股
    base_codes = [("2330", "台積電"), ("2317", "鴻海"), ("2382", "廣達"), ("2454", "聯發科")]
    seen = {c for c, _ in base_codes}
    top_traded = sorted(twse_map.items(), key=lambda x: x[1]["val"], reverse=True)
    for code, info in top_traded:
        if not code.startswith("00") and code not in seen and len(base_codes) < 6:
            base_codes.append((code, info["name"]))
            seen.add(code)

    stock_cards = []
    for code, name in base_codes:
        news_list = fetch_google_news_rss(f"{code} {name} 營收 OR 法人 OR 財報", now, max_items=2)
        price_str = ""
        if code in twse_map:
            c_info = twse_map[code]
            price_str = f"【最新收盤：{c_info['close']} 元｜漲跌：{c_info['change']}】"
        if news_list:
            top_n = news_list[0]
            second_title = f"；另市場關注「{news_list[1]['title']}」" if len(news_list) > 1 else ""
            stock_cards.append({
                "code_and_name": f"{code} {name}",
                "content": f"{price_str}{top_n['title']}{second_title}。",
                "source_name": top_n["source_name"],
                "source_url": top_n["source_url"],
                "image_url": top_n["image_url"],
                "published_at": top_n["published_at"]
            })
        else:
            stock_cards.append({
                "code_and_name": f"{code} {name}",
                "content": f"{price_str}持續追蹤近期營收財報與三大法人籌碼動向。",
                "source_name": "臺灣證券交易所",
                "source_url": "https://www.twse.com.tw/"
            })
    return stock_cards

# 若 Gemini API 可用則協助潤飾摘要；若遇 429 額度限制或型號停用則自動使用抓好的新聞，絕不報錯中斷！
def try_gemini_polish(raw_data, today_str):
    api_key = os.environ.get("GEMINI_API_KEY", "").strip()
    if not api_key:
        return raw_data
    try:
        from google import genai
        client = genai.Client(api_key=api_key)
        prompt = f"""
請將以下今日（{today_str}）抓取的真實新聞資料潤飾得更流暢，保留原本所有的網址(source_url)、圖片(image_url)與媒體名稱(source_name)，只輸出純 JSON：
{json.dumps(raw_data, ensure_ascii=False)}
"""
        for model_name in ["gemini-3.8-flash", "gemini-2.0-flash", "gemini-1.5-flash"]:
            try:
                resp = client.models.generate_content(model=model_name, contents=prompt)
                txt = re.sub(r"^```json\s*|\s*```$", "", resp.text.strip())
                polished = json.loads(txt)
                for section in ("industry", "market_overview", "focus_stocks", "politics"):
                    original_items = raw_data[section]
                    revised_items = polished[section]
                    if len(original_items) != len(revised_items):
                        raise ValueError(f"Changed number of items in {section}")
                    for original, revised in zip(original_items, revised_items):
                        if revised.get("source_url") != original["source_url"]:
                            raise ValueError(f"Changed source or order in {section}")
                        content = revised.get("content")
                        if not isinstance(content, str) or not content.strip():
                            raise ValueError(f"Missing summary in {section}")
                for section in ("industry", "market_overview", "politics"):
                    for original, revised in zip(raw_data[section], polished[section]):
                        original["content"] = revised["content"]
                return raw_data
            except Exception as inner_e:
                print(f"Model {model_name} skipped: {inner_e}")
    except Exception as e:
        print(f"Gemini polish skipped (using direct RSS data): {e}")
    return raw_data

def generate_daily_news():
    now = datetime.datetime.now(TAIPEI)
    today_str = now.strftime("%Y年%m月%d日")
    now_full = now.strftime("%Y-%m-%d %H:%M")

    industry = fetch_yahoo_news_with_images(
        "https://tw.stock.yahoo.com/rss?category=research", now, max_items=6
    )
    industry += fetch_google_news_rss("半導體 OR AI晶片 OR 科技產業 財經", now, max_items=6)

    market_overview = fetch_yahoo_news_with_images(
        "https://tw.stock.yahoo.com/rss?category=tw-market", now, max_items=2
    )
    if len(market_overview) < 2:
        market_overview += fetch_google_news_rss("台股 加權指數 外資 投信", now, max_items=4)

    focus_stocks = fetch_focus_stocks(now)

    politics = fetch_yahoo_news_with_images("https://tw.news.yahoo.com/rss/politics", now)
    politics += fetch_google_news_rss("台灣 政治 OR 國際外交 要聞", now, max_items=4)

    data = {
        "greeting": f"為你整理截至 {today_str} 最近 48 小時的產業、股市、台股個股與政治新聞：",
        "industry": select_news(industry, 4),
        "market_overview": select_news(market_overview, 2),
        "focus_stocks": focus_stocks,
        "politics": select_news(politics, 4),
        "disclaimer": "以上市場觀察與焦點個股整理僅供資訊參考，不構成任何形式之投資建議與邀約，投資人應獨立審慎評估風險並自負盈虧。"
    }

    with ThreadPoolExecutor(max_workers=6) as executor:
        cards = data["industry"] + data["market_overview"] + data["politics"]
        cards += [item for item in focus_stocks if "published_at" in item]
        list(executor.map(add_preview_image, cards))

    data = try_gemini_polish(data, today_str)
    return data, today_str, now_full

def build_html(data, today_str, now_full):
    def render_image(item, label):
        url = item.get("image_url")
        if not url:
            return ""
        return (
            f'<div class="news-img"><img src="{escape(url)}" alt="{escape(label)}" '
            'loading="lazy" onerror="this.parentElement.remove()"></div>'
        )

    def render_source(item):
        link = item.get("source_url")
        source = escape(item.get("source_name", "新聞來源"))
        if not link:
            return ""
        return f'（<a href="{escape(link)}" target="_blank" rel="noopener noreferrer">{source}</a>）'

    def render_date(item):
        if not item.get("published_at"):
            return ""
        published = datetime.datetime.fromisoformat(item["published_at"]).astimezone(TAIPEI)
        return (
            f'<time datetime="{published.isoformat()}">'
            f'{published:%Y-%m-%d %H:%M}（台灣時間）</time>'
        )

    def render_news_items(items):
        if not items:
            return "<p>目前沒有符合最近 48 小時條件的報導。</p>"
        return "".join(f"""
            <article class="news-item">
              {render_image(item, item["title"])}
              <div class="news-text">
                <h3>{escape(item["title"])}</h3>
                {render_date(item)}
                <p>{escape(item["content"])}{render_source(item)}</p>
              </div>
            </article>""" for item in items)

    def render_stock_items(items):
        return "".join(f"""
            <div class="stock-card">
              <div class="stock-header">{escape(item["code_and_name"])}</div>
              {render_image(item, item["code_and_name"])}
              {render_date(item)}
              <p>{escape(item["content"])}{render_source(item)}</p>
            </div>""" for item in items)

    return f"""<!DOCTYPE html>
<html lang="zh-Hant">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>每日重點新聞摘要｜{today_str}</title>
  <style>
    :root {{
      --bg: #F8F9FA; --card: #FFFFFF; --ink: #1F2937; --body: #374151;
      --mute: #6B7280; --border: #E5E7EB; --accent: #1D4ED8; --tag-bg: #EFF6FF;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0; padding: 32px clamp(16px, 4vw, 48px) 60px;
      background: var(--bg); color: var(--ink);
      font-family: "Microsoft JhengHei", "PingFang TC", sans-serif; line-height: 1.7;
    }}
    .container {{ max-width: 960px; margin: 0 auto; }}
    .header-box {{
      background: var(--card); padding: 24px 28px; border-radius: 12px;
      border: 1px solid var(--border); margin-bottom: 28px;
    }}
    .update-time {{ font-size: 13px; color: var(--mute); margin-bottom: 6px; }}
    h1 {{ font-size: clamp(22px, 3.2vw, 30px); margin: 0 0 10px; color: var(--ink); }}
    .greeting {{ font-size: 16px; color: var(--body); margin: 0; }}
    h2 {{
      font-size: 21px; margin: 36px 0 16px; padding-left: 12px;
      border-left: 4px solid var(--accent); color: var(--ink);
    }}
    .sub-label {{ font-size: 16px; font-weight: 700; color: var(--accent); margin: 20px 0 12px; }}
    .news-list {{ display: flex; flex-direction: column; gap: 16px; }}
    .news-item {{
      display: grid; grid-template-columns: 220px 1fr; gap: 20px;
      background: var(--card); border: 1px solid var(--border);
      border-radius: 10px; padding: 18px; align-items: start;
    }}
    .news-item:not(:has(.news-img)) {{ grid-template-columns: 1fr; }}
    @media (max-width: 680px) {{ .news-item {{ grid-template-columns: 1fr; }} }}
    .news-img img {{ width: 100%; height: 140px; object-fit: cover; border-radius: 8px; display: block; }}
    time {{ display: block; margin: 0 0 5px; color: var(--mute); font-size: 13px; }}
    .stock-card .news-img {{ margin: 8px 0; }}
    .news-text h3 {{ font-size: 17px; margin: 0 0 8px; color: var(--ink); }}
    .news-text p {{ font-size: 15px; margin: 0; color: var(--body); }}
    .stock-grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(280px, 1fr)); gap: 16px; }}
    .stock-card {{ background: var(--card); border: 1px solid var(--border); border-radius: 10px; padding: 18px; }}
    .stock-header {{
      display: inline-block; background: var(--tag-bg); color: var(--accent);
      font-weight: 700; font-size: 16px; padding: 4px 12px; border-radius: 6px; margin-bottom: 10px;
    }}
    .stock-card p {{ font-size: 15px; margin: 0; color: var(--body); }}
    a {{ color: var(--accent); text-decoration: none; font-weight: 500; }}
    a:hover {{ text-decoration: underline; }}
    .disclaimer {{
      margin-top: 40px; padding: 16px 20px; background: #FEF3C7;
      border-left: 4px solid #D97706; border-radius: 8px; font-size: 14px; color: #92400E;
    }}
  </style>
</head>
<body>
  <div class="container">
    <div class="header-box">
      <div class="update-time">最後自動更新：{now_full}（台灣時間）</div>
      <h1>整理每日重點新聞</h1>
      <p class="greeting">{data.get('greeting', '')}</p>
    </div>

    <h2>一、 產業新聞</h2>
    <div class="news-list">{render_news_items(data.get('industry', []))}</div>

    <h2>二、 股市新聞與台股重點個股觀察</h2>
    <div class="sub-label">總體市場動態：</div>
    <div class="news-list">{render_news_items(data.get('market_overview', []))}</div>

    <div class="sub-label">重點個股觀察：</div>
    <div class="stock-grid">{render_stock_items(data.get('focus_stocks', []))}</div>

    <h2>三、 政治新聞</h2>
    <div class="news-list">{render_news_items(data.get('politics', []))}</div>

    <div class="disclaimer"><strong>警語：</strong>{data.get('disclaimer', '')}</div>
  </div>
</body>
</html>"""

if __name__ == "__main__":
    data, today_str, now_full = generate_daily_news()
    html_out = build_html(data, today_str, now_full)
    with open("index.html", "w", encoding="utf-8") as f:
        f.write(html_out)
    print("index.html updated successfully.")
