import os
import json
import datetime
import urllib.request
import re
from google import genai
from google.genai import types

# 從新聞原文網址自動抓取新聞代表照片 (og:image)
def fetch_article_image(url):
    if not url:
        return ""
    try:
        req = urllib.request.Request(
            url,
            headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
        )
        with urllib.request.urlopen(req, timeout=8) as resp:
            html = resp.read().decode("utf-8", errors="ignore")
        m = re.search(r'<meta[^>]+property=["\']og:image["\'][^>]+content=["\']([^"\']+)["\']', html, re.I)
        if not m:
            m = re.search(r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:image["\']', html, re.I)
        return m.group(1) if m else ""
    except Exception:
        return ""

def generate_daily_news():
    api_key = os.environ["GEMINI_API_KEY"]
    client = genai.Client(api_key=api_key)

    tz_tw = datetime.timezone(datetime.timedelta(hours=8))
    today_str = datetime.datetime.now(tz_tw).strftime("%Y年%m月%d日")
    now_full = datetime.datetime.now(tz_tw).strftime("%Y-%m-%d %H:%M")

    # 完全比照原本「整理每日重點新聞」的指令與分類規則
    prompt = f"""
今天是 {today_str}。請完全依照以下原本的每日重點新聞規則執行：

目標：每日整理國內外重要的產業新聞、股市動態（包含台股重點焦點個股與股票代碼）及政治新聞。

執行步驟：
1. 搜尋過去 24 小時內國內外主要的產業動態、股市與財經新聞，以及重大政治新聞。
2. 針對台股權值股與當前市場熱門焦點股（例如：2330 台積電、2317 鴻海、2382 廣達、2454 聯發科及當日盤面熱門焦點股等），整理其近期營收財報、法人動向與市場觀察亮點並標註股票代碼。
3. 將蒐集到的資訊歸納整理為三個主要類別：
   - 一、產業新聞
   - 二、股市新聞與台股重點個股觀察（包含「總體市場動態」與「重點個股觀察」，附股票代碼與觀點趨勢）
   - 三、政治新聞
4. 附上警語，說明市場觀察僅供資訊參考，不構成投資建議。

請將整理結果以純 JSON 格式輸出（不要加 ```json 標記），格式如下：
{{
  "greeting": "早安！為你整理今日（{today_str}）國內外重點產業、股市、台股焦點個股與政治新聞摘要：",
  "industry": [
    {{
      "title": "新聞重點標題",
      "content": "完整重點內容說明",
      "source_name": "媒體名稱",
      "source_url": "新聞原文網址"
    }}
  ],
  "market_overview": [
    {{
      "title": "總體市場動態標題",
      "content": "台股與美股總體盤勢重點說明",
      "source_name": "媒體名稱",
      "source_url": "新聞原文網址"
    }}
  ],
  "focus_stocks": [
    {{
      "code_and_name": "股票代碼與名稱（如 2330 台積電）",
      "content": "近期營收財報、法人動向與市場觀察亮點",
      "source_name": "媒體名稱",
      "source_url": "新聞原文網址"
    }}
  ],
  "politics": [
    {{
      "title": "政治新聞重點標題",
      "content": "完整重點內容說明",
      "source_name": "媒體名稱",
      "source_url": "新聞原文網址"
    }}
  ],
  "disclaimer": "以上市場觀察與焦點個股整理僅供資訊參考，不構成任何形式之投資建議與邀約，投資人應獨立審慎評估風險並自負盈虧。"
}}
"""

    # 啟用 Google Search 即時搜尋工具
    response = client.models.generate_content(
        model="gemini-2.5-flash",
        contents=prompt,
        config=types.GenerateContentConfig(
            tools=[types.Tool(google_search=types.GoogleSearch())]
        )
    )

    raw_text = response.text.strip()
    raw_text = re.sub(r"^```json\s*|\s*```$", "", raw_text)
    data = json.loads(raw_text)

    # 為每則新聞自動抓取原本的新聞照片 (og:image)
    for section in ["industry", "market_overview", "politics"]:
        for item in data.get(section, []):
            item["image_url"] = fetch_article_image(item.get("source_url", ""))

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
      --bg: #F8F9FA;
      --card: #FFFFFF;
      --ink: #1F2937;
      --body: #374151;
      --mute: #6B7280;
      --border: #E5E7EB;
      --accent: #1D4ED8;
      --tag-bg: #EFF6FF;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      padding: 32px clamp(16px, 4vw, 48px) 60px;
      background: var(--bg);
      color: var(--ink);
      font-family: "Microsoft JhengHei", "PingFang TC", sans-serif;
      line-height: 1.7;
    }}
    .container {{ max-width: 960px; margin: 0 auto; }}
    .header-box {{
      background: var(--card);
      padding: 24px 28px;
      border-radius: 12px;
      border: 1px solid var(--border);
      margin-bottom: 28px;
    }}
    .update-time {{ font-size: 13px; color: var(--mute); margin-bottom: 6px; }}
    h1 {{ font-size: clamp(22px, 3.2vw, 30px); margin: 0 0 10px; color: var(--ink); }}
    .greeting {{ font-size: 16px; color: var(--body); margin: 0; }}
    h2 {{
      font-size: 21px;
      margin: 36px 0 16px;
      padding-left: 12px;
      border-left: 4px solid var(--accent);
      color: var(--ink);
    }}
    .sub-label {{
      font-size: 16px;
      font-weight: 700;
      color: var(--accent);
      margin: 20px 0 12px;
    }}
    .news-list {{ display: flex; flex-direction: column; gap: 16px; }}
    .news-item {{
      display: grid;
      grid-template-columns: 220px 1fr;
      gap: 20px;
      background: var(--card);
      border: 1px solid var(--border);
      border-radius: 10px;
      padding: 18px;
      align-items: start;
    }}
    .news-item:not(:has(.news-img)) {{ grid-template-columns: 1fr; }}
    @media (max-width: 680px) {{
      .news-item {{ grid-template-columns: 1fr; }}
    }}
    .news-img img {{
      width: 100%;
      height: 140px;
      object-fit: cover;
      border-radius: 8px;
      display: block;
    }}
    .news-text h3 {{ font-size: 17px; margin: 0 0 8px; color: var(--ink); }}
    .news-text p {{ font-size: 15px; margin: 0; color: var(--body); }}
    .stock-grid {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(280px, 1fr));
      gap: 16px;
    }}
    .stock-card {{
      background: var(--card);
      border: 1px solid var(--border);
      border-radius: 10px;
      padding: 18px;
    }}
    .stock-header {{
      display: inline-block;
      background: var(--tag-bg);
      color: var(--accent);
      font-weight: 700;
      font-size: 16px;
      padding: 4px 12px;
      border-radius: 6px;
      margin-bottom: 10px;
    }}
    .stock-card p {{ font-size: 15px; margin: 0; color: var(--body); }}
    a {{ color: var(--accent); text-decoration: none; font-weight: 500; }}
    a:hover {{ text-decoration: underline; }}
    .disclaimer {{
      margin-top: 40px;
      padding: 16px 20px;
      background: #FEF3C7;
      border-left: 4px solid #D97706;
      border-radius: 8px;
      font-size: 14px;
      color: #92400E;
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
    <div class="news-list">
      {render_news_items(data.get('industry', []))}
    </div>

    <h2>二、 股市新聞與台股重點個股觀察</h2>
    <div class="sub-label">總體市場動態：</div>
    <div class="news-list">
      {render_news_items(data.get('market_overview', []))}
    </div>

    <div class="sub-label">重點個股觀察：</div>
    <div class="stock-grid">
      {render_stock_items(data.get('focus_stocks', []))}
    </div>

    <h2>三、 政治新聞</h2>
    <div class="news-list">
      {render_news_items(data.get('politics', []))}
    </div>

    <div class="disclaimer">
      <strong>警語：</strong>{data.get('disclaimer', '')}
    </div>
  </div>
</body>
</html>"""

if __name__ == "__main__":
    data, today_str, now_full = generate_daily_news()
    html_out = build_html(data, today_str, now_full)
    with open("index.html", "w", encoding="utf-8") as f:
        f.write(html_out)
    print("index.html updated successfully.")
