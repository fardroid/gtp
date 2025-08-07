from flask import Flask, request, jsonify
from newspaper import Article
import requests
from playwright.sync_api import sync_playwright

app = Flask(__name__)
app.debug = True

# --- Получение трендов через Playwright ---
import re

def parse_searches(text):
    """
    Преобразует '20 тыс.' → 20000, '200+' → 200
    """
    text = text.lower().replace('+', '').strip()
    match = re.search(r'(\d+)\s*тыс', text)
    if match:
        return int(match.group(1)) * 1000
    match = re.search(r'(\d+)', text)
    if match:
        return int(match.group(1))
    return None

def get_google_trends():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto("https://trends.google.com/trending?geo=RU&hl=ru", timeout=60000)

        page.wait_for_selector("#trend-table td.jvkLtd", timeout=60000)
        raw_titles = page.locator("#trend-table td.jvkLtd").all_text_contents()
        browser.close()

        results = []
        for t in raw_titles:
            t = t.strip()
            if "Поисковых запросов" in t:
                parts = t.split("Поисковых запросов")
                title = parts[0].strip()
                searches = parse_searches(parts[1])
                results.append({
                    "title": title,
                    "searches": searches
                })

        return results



@app.route('/trends', methods=['GET'])
def trends_endpoint():
    try:
        trends = get_google_trends()
        return jsonify(trends)
    except Exception as e:
        return jsonify({
            "error": str(e),
            "trends": [
                "SpaceX запускает Starship",
                "Выборы в США",
                "Apple iPhone 16 презентация"
            ]
        })

# --- Поиск новостей ---
@app.route('/news', methods=['GET'])
def get_news():
    query = request.args.get('query')
    if not query:
        return jsonify({"error": "query param is required"}), 400

    api_key = "AIzaSyD2m-KVtY94rCDPSX7Utxl23LQsGt_EtDs"
    cx = "e6822b7d3afb14250"
    date_filter = "dateRestrict=d3"
    try:
        url = f"https://www.googleapis.com/customsearch/v1?q={query}&cx={cx}&key={api_key}&tbm=nws&{date_filter}"
        resp = requests.get(url)
        results = resp.json().get('items', [])

        articles = []
        for r in results[:5]:
            try:
                art = Article(r['link'])
                art.download()
                art.parse()
                articles.append({
                    "title": art.title,
                    "text": art.text[:1000] + "...",
                    "url": r['link'],
                    "published_at": art.publish_date.isoformat() if art.publish_date else None,
                    "image_url": r.get("pagemap", {}).get("cse_image", [{}])[0].get("src")

                })
            except Exception as e:
                continue

        if not articles:
            return jsonify({"message": "Новости не найдены"})

        return jsonify(articles)

    except Exception as e:
        return jsonify({"error": str(e)})

import os

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))  # Берём порт из окружения
    app.run(host="0.0.0.0", port=port)
