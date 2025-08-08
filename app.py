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
    cx = "154464994ff404d2f"
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

from PIL import Image, ImageDraw, ImageFont
from io import BytesIO
import base64

@app.route('/image', methods=['GET'])
def image_search():
    query = request.args.get("query")
    overlay_text = request.args.get("text", "")

    if not query:
        return jsonify({"error": "query param is required"}), 400

    api_key = "AIzaSyBmbtPHA8K5ysCrjRHdHMuxqfhxMGNAOsY"
    cx = "e6822b7d3afb14250"

    try:
        url = (
            f"https://www.googleapis.com/customsearch/v1"
            f"?q={query}&cx={cx}&key={api_key}&searchType=image&num=5"
        )
        resp = requests.get(url)
        results = resp.json().get("items", [])

        if not results:
            return jsonify({"error": "No image results found"}), 404

        # Берём первую подходящую картинку
        image_url = results[0]["link"]
        image_resp = requests.get(image_url, timeout=10)

        # Загружаем изображение
        image = Image.open(BytesIO(image_resp.content)).convert("RGBA")
        draw = ImageDraw.Draw(image)

        # Настрой шрифт
        try:
            font = ImageFont.truetype("DejaVuSans.ttf", 36)
        except:
            font = ImageFont.load_default()

        # Подложка под текст
        bbox = font.getbbox(overlay_text)
        text_width = bbox[2] - bbox[0]
        text_height = bbox[3] - bbox[1]

        padding = 20
        box_x = 30
        box_y = image.height - text_height - 2 * padding

        overlay = Image.new("RGBA", image.size, (0, 0, 0, 0))
        overlay_draw = ImageDraw.Draw(overlay)
        overlay_draw.rectangle(
            [box_x, box_y, box_x + text_width + 2 * padding, box_y + text_height + 2 * padding],
            fill=(0, 0, 0, 160)
        )

        # Объединяем подложку и изображение
        image = Image.alpha_composite(image, overlay)

        # Поверх подложки — текст
        draw = ImageDraw.Draw(image)
        draw.text((box_x + padding, box_y + padding), overlay_text, font=font, fill=(255, 255, 255, 255))

        # Сохраняем в память
        buffer = BytesIO()
        image.convert("RGB").save(buffer, format="JPEG")
        encoded_image = base64.b64encode(buffer.getvalue()).decode("utf-8")

        return jsonify({
            "image_url": image_url,
            "overlayed_base64": f"data:image/jpeg;base64,{encoded_image}"
        })

    except Exception as e:
        return jsonify({"error": str(e)}), 500



import os

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))  # Берём порт из окружения
    app.run(host="0.0.0.0", port=port)
