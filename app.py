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


from PIL import Image, ImageDraw, ImageFont, ImageOps
from io import BytesIO
import base64


@app.route('/image', methods=['GET'])
def image_search():
    query = request.args.get("query")
    overlay_text = request.args.get("text", "")

    if not query:
        return jsonify({"error": "query param is required"}), 400

    api_key = "AIzaSyBmbtPHA8K5ysCrjRHdHMuxqfhxMGNAOsY"
    cx = "154464994ff404d2f"

    try:
        # 1. Поиск изображений
        url = f"https://www.googleapis.com/customsearch/v1?q={query}&cx={cx}&key={api_key}&searchType=image&num=5"
        resp = requests.get(url)
        results = resp.json().get("items", [])

        if not results:
            return jsonify({"message": "No image results found"}), 204

        # 2. Перебираем картинки и ищем рабочую
        headers = {
            "User-Agent": "Mozilla/5.0 (compatible; NewsBot/1.0)"
        }

        image_url = None
        image_data = None

        for result in results:
            try:
                candidate_url = result["link"]
                r = requests.get(candidate_url, headers=headers, timeout=10, verify=False)

                if r.status_code == 200 and "image" in r.headers.get("Content-Type", ""):
                    image_url = candidate_url
                    image_data = r.content
                    break
            except Exception:
                continue  # пробуем следующую

        if image_data is None:
            return jsonify({"message": "No valid image found"}), 204

        # 3. Генерация изображения с текстом
        target_size = (1280, 720)
        image = Image.open(BytesIO(image_data)).convert("RGBA")
        image = ImageOps.fit(image, target_size, method=Image.LANCZOS, centering=(0.5, 0.5))
        # Настройка шрифта
        try:
            font_size = int(target_size[1] * 0.2)
            font = ImageFont.truetype("fonts/Roboto-Bold.ttf", font_size)
        except:
            font = ImageFont.load_default(font_size)
        # === ЛЕВОЕ ВЫРАВНИВАНИЕ + ПЕРЕНОСЫ СТРОК ===
        draw = ImageDraw.Draw(image)

        W, H = target_size
        margin = 40  # внешний отступ от левого/нижнего края
        padding = 28  # внутренние отступы внутри подложки
        line_spacing = max(12, int((font.size if hasattr(font, "size") else 40) * 0.2))

        # максимально допустимая ширина текста (не вся ширина экрана, а с полями)
        max_text_width = W - 2 * margin - 2 * padding

        # переносим текст по ширине пикселями
        def wrap_by_width(text, font, max_w, draw):
            words = text.split()
            lines, cur = [], []
            for w in words:
                trial = (' '.join(cur + [w])).strip()
                x0, y0, x1, y1 = draw.textbbox((0, 0), trial, font=font)
                if (x1 - x0) <= max_w:
                    cur.append(w)
                else:
                    if cur:
                        lines.append(' '.join(cur))
                        cur = [w]
                    else:
                        # если одно слово длиннее строки — кладём как есть
                        lines.append(w)
                        cur = []
            if cur:
                lines.append(' '.join(cur))
            return lines

        lines = wrap_by_width(overlay_text or "", font, max_text_width, draw)

        # метрики строки
        ascent, descent = font.getmetrics()
        line_h = ascent + descent

        # ширина = ширина самой длинной линии; высота = сумма высот + межстрочные зазоры
        line_widths = []
        for ln in lines:
            x0, y0, x1, y1 = draw.textbbox((0, 0), ln, font=font)
            line_widths.append(x1 - x0)
        content_w = max(line_widths) if line_widths else 0
        content_h = len(lines) * line_h + max(0, len(lines) - 1) * line_spacing

        # координаты подложки: СЛЕВА, ВНИЗУ
        box_x0 = margin
        box_y0 = H - margin - (content_h + 2 * padding)
        box_x1 = box_x0 + content_w + 2 * padding
        box_y1 = box_y0 + content_h + 2 * padding

        # рисуем полупрозрачную серую подложку
        overlay_img = Image.new("RGBA", target_size, (0, 0, 0, 0))
        overlay_draw = ImageDraw.Draw(overlay_img)
        overlay_draw.rectangle([box_x0, box_y0, box_x1, box_y1], fill=(0, 0, 0, 160))

        image = Image.alpha_composite(image, overlay_img)

        # рисуем текст слева-сверху, построчно (никаких anchor/центров)
        tx = box_x0 + padding
        ty = box_y0 + padding
        for ln in lines:
            draw.text((tx, ty), ln, font=font, fill=(255, 255, 255, 255))
            ty += line_h + line_spacing

        # Конвертация в base64 (оставь как у тебя далее)
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
