from flask import Flask, request, jsonify
from newspaper import Article
import requests
from playwright.sync_api import sync_playwright

app = Flask(__name__)
app.debug = True

# --- Получение трендов через Playwright ---
def get_google_trends():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto("https://trends.google.com/trending?geo=RU&hl=ru", timeout=60000)

        page.wait_for_selector("#trend-table td.jvkLtd", timeout=60000)
        raw_titles = page.locator("#trend-table td.jvkLtd").all_text_contents()
        browser.close()

        clean_titles = []
        for t in raw_titles:
            t = t.strip()
            if "Поисковых запросов" in t:
                t = t.split("Поисковых запросов")[0]
            if t:
                clean_titles.append(t.strip())

        return clean_titles


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

    try:
        url = f"https://www.googleapis.com/customsearch/v1?q={query}&cx={cx}&key={api_key}&tbm=nws"
        resp = requests.get(url)
        results = resp.json().get('items', [])

        articles = []
        for r in results[:3]:
            try:
                art = Article(r['link'])
                art.download()
                art.parse()
                articles.append({
                    "title": art.title,
                    "text": art.text[:500] + "...",
                    "url": r['link']
                })
            except Exception as e:
                articles.append({
                    "title": r.get('title', 'Без названия'),
                    "text": f"Ошибка парсинга: {str(e)}",
                    "url": r['link']
                })

        if not articles:
            return jsonify({"message": "Новости не найдены"})

        return jsonify(articles)

    except Exception as e:
        return jsonify({"error": str(e)})

if __name__ == "__main__":
    app.run(host='0.0.0.0')
