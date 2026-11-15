import os
import json
import datetime
import urllib.request
import urllib.parse
import xml.etree.ElementTree as ET
import re

# 從新聞網頁或 Google News 抓取配圖與摘要
def fetch_google_news_rss(query, max_items=5):
    encoded_q = urllib.parse.quote(query)
    url = f"https://news.google.com/rss/search?q={encoded_q}+when:1d&hl=zh-TW&gl=TW&ceid=TW:zh-Hant"
    items = []
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=12) as resp:
            xml_data = resp.read()
        root = ET.fromstring(xml_data)
        for item in root.findall("./channel/item")[:max_items]:
            full_title = item.findtext("title", "").strip()
            link = item.findtext("link", "").strip()
            source_el = item.find("source")
            source_name = source_el.text.strip() if source_el is not None and source_el.text else "新聞來源"
            # 分離標題與媒體名稱
            title = full_title.rsplit(" - ", 1)[0] if " - " in full_title else full_title
            desc_raw = item.findtext("description", "")
            desc_clean = re.sub(r"<[^>]+>", " ", desc_raw)
            desc_clean = re.sub(r"\s+", " ", desc_clean).strip()
            items.append({
                "title": title,
                "content": desc_clean if desc_clean and desc_clean != title else f"點擊下方來源連結閱讀「{title}」完整報導。",
                "source_name": source_name,
                "source_url": link,
                "image_url": ""
            })
    except Exception as e:
        print(f"RSS fetch warning for {query}: {e}")
    return items

# 抓取 Yahoo 奇摩股市新聞（自帶高畫質新聞原圖）
def fetch_yahoo_news_with_images(rss_url, max_items=4):
    items = []
    try:
        req = urllib.request.Request(rss_url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=12) as resp:
            xml_data = resp.read()
        root = ET.fromstring(xml_data)
        for item in root.findall("./channel/item")[:max_items]:
            title = item.findtext("title", "").strip()
            link = item.findtext("link", "").strip()
            desc = re.sub(r"<[^>]+>", "", item.findtext("description", "")).strip()
            img_url = ""
            media = item.find("{http://search.yahoo.com/mrss/}content")
            if media is not None and media.attrib.get("url"):
                img_url = media.attrib.get("url")
            items.append({
                "title": title,
                "content": desc[:160] + ("..." if len(desc) > 160 else ""),
                "source_name": "Yahoo奇摩財經",
                "source_url": link,
                "image_url": img_url
            })
    except Exception as e:
        print(f"Yahoo RSS warning: {e}")
    return items

# 抓取台灣證交所最新收盤價，自動結合個股最新新聞
def fetch_focus_stocks():
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
        news_list = fetch_google_news_rss(f"{code} {name} 營收 OR 法人 OR 財報", max_items=2)
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
                "source_url": top_n["source_url"]
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
                return json.loads(txt)
            except Exception as inner_e:
                print(f"Model {model_name} skipped: {inner_e}")
    except Exception as e:
        print(f"Gemini polish skipped (using direct RSS data): {e}")
    return raw_data

def generate_daily_news():
    tz_tw = datetime.timezone(datetime.timedelta(hours=8))
    today_str = datetime.datetime.now(tz_tw).strftime("%Y年%m月%d日")
    now_full = datetime.datetime.now(tz_tw).strftime("%Y-%m-%d %H:%M")

    # 1. 產業新聞（結合 Yahoo 附圖新聞 + Google News 過去24小時產業焦點）
    industry = fetch_yahoo_news_with_images("https://tw.stock.yahoo.com/rss?category=research", max_items=2)
    industry += fetch_google_news_rss("半導體 OR AI晶片 OR 科技產業 財經", max_items=3)

    # 2. 股市總體動態
    market_overview = fetch_yahoo_news_with_images("https://tw.stock.yahoo.com/rss?category=tw-market", max_items=2)
    if not market_overview:
        market_overview = fetch_google_news_rss("台股 加權指數 外資 投信", max_items=2)

    # 3. 台股重點個股觀察
    focus_stocks = fetch_focus_stocks()

    # 4. 政治新聞
    politics = fetch_yahoo_news_with_images("https://tw.news.yahoo.com/rss/politics", max_items=2)
    politics += fetch_google_news_rss("台灣 政治 OR 國際外交 要聞", max_items=2)

    data = {
        "greeting": f"早安！為你整理今日（{today_str}）國內外重點產業、股市、台股焦點個股與政治新聞摘要：",
        "industry": industry[:4],
        "market_overview": market_overview[:2],
        "focus_stocks": focus_stocks,
        "politics": politics[:4],
        "disclaimer": "以上市場觀察與焦點個股整理僅供資訊參考，不構成任何形式之投資建議與邀約，投資人應獨立審慎評估風險並自負盈虧。"
    }

    data = try_gemini_polish(data, today_str)
    return data, today_str, now_full

def build_html(data, today_str, now_full):
    def render_news_items(items):
        html = ""
        for it in items:
            img_html = ""
            if it.get("image_url"):
                img_html = f'<div class="news-img"><img src="{it["image_url"]}" alt="{it["title"]}" loading="lazy" onerror="this.parentElement.style.display=\'none\'"></div>'
            link_html = f'（<a href="{it["source_url"]}" target="_blank" rel="noopener noreferrer">{it.get("source_name", "新聞來源")}</a>）' if it.get("source_url") else ""
            html += f"""
            <article class="news-item">
              {img_html}
              <div class="news-text">
                <h3>{it['title']}</h3>
                <p>{it['content']}{link_html}</p>
              </div>
            </article>"""
        return html

    def render_stock_items(items):
        html = ""
        for st in items:
            link_html = f'（<a href="{st["source_url"]}" target="_blank" rel="noopener noreferrer">{st.get("source_name", "新聞來源")}</a>）' if st.get("source_url") else ""
            html += f"""
            <div class="stock-card">
              <div class="stock-header">{st['code_and_name']}</div>
              <p>{st['content']}{link_html}</p>
            </div>"""
        return html

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
