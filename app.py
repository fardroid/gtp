from flask import Flask, request, jsonify
from newspaper import Article
import requests
from playwright.sync_api import sync_playwright

app = Flask(__name__)
app.debug = False

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
        browser = p.chromium.launch(
            headless=True,
            args=[
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--disable-dev-shm-usage",
                "--disable-gpu",
            ],
        )
        page = browser.new_page()
        page.goto("https://trends.google.com/trending?geo=RU&hl=ru&sort=search-volume&hours=4&status=active", timeout=60000)

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
        app.logger.exception("Failed to fetch trends")
        return jsonify({"error": "failed_to_fetch_trends", "details": str(e)}), 500


# --- Поиск новостей ---
@app.route('/news', methods=['GET'])
def get_news():
    query = request.args.get('query')
    if not query:
        return jsonify({"error": "query param is required"}), 400

    api_key = "AIzaSyD2m-KVtY94rCDPSX7Utxl23LQsGt_EtDs"
    cx = "e6822b7d3afb14250"

    try:
        url = "https://www.googleapis.com/customsearch/v1"
        params = {
            "q": query,
            "cx": cx,
            "key": api_key,
            "tbm": "nws",
            "dateRestrict": "d3",
            "num": 10,
        }
        resp = requests.get(url, params=params, timeout=15)
        data = resp.json() if resp.content else {}

        # --- Явная обработка превышения квоты ---
        # Google иногда шлёт 429, иногда 403 с текстом про квоту.
        if resp.status_code == 429:
            return jsonify({"error": f"превышен лимит запросов.\n {data} "}), 429

        err = data.get("error")
        if err:
            code = err.get("code")
            status = err.get("status", "")
            reason_top = err.get("reason", "")
            reasons = {e.get("reason") for e in err.get("errors", []) if isinstance(e, dict)}

            # Признаки rate limit / quota exceeded
            rate_limited = (
                code == 429
                or status == "RESOURCE_EXHAUSTED"
                or reason_top == "rateLimitExceeded"
                or "rateLimitExceeded" in reasons
                or any(
                    (d.get("reason") in ("RATE_LIMIT_EXCEEDED", "QUOTA_EXCEEDED"))
                    for d in err.get("details", [])
                    if isinstance(d, dict)
                )
            )
            if rate_limited:
                return jsonify({"error": f"превышен лимит запросов: code: {code}, status: {status}, reasons: {reasons}"}), 429

            # Любая другая ошибка от Google
            return jsonify({"error": f"google api error: {err.get('message', 'unknown error')}"}), code or 502

        # --- Нормальный кейс: парсим результаты ---
        results = data.get('items', []) or []
        articles = []
        for r in results[:5]:
            try:
                art = Article(r['link'])
                art.download()
                art.parse()
                articles.append({
                    "title": art.title,
                    "text": (art.text[:1000] + "...") if art.text else None,
                    "url": r['link'],
                    "published_at": art.publish_date.isoformat() if art.publish_date else None,
                    "image_url": r.get("pagemap", {}).get("cse_image", [{}])[0].get("src")
                })
            except Exception:
                continue

        if not articles:
            return jsonify({"message": "Новости не найдены"})

        return jsonify(articles)

    except requests.Timeout:
        return jsonify({"error": "превышено время ожидания запроса к Google"}), 504
    except Exception as e:
        return jsonify({"error": str(e)}), 500


from PIL import Image, ImageDraw, ImageFont, ImageOps
from io import BytesIO
import base64


from flask import send_file, jsonify, request
import re, base64
from io import BytesIO
import requests
from PIL import Image, ImageDraw, ImageFont, ImageOps

@app.route('/image', methods=['GET'])
def image_search():
    query = request.args.get("query")
    overlay_text = (request.args.get("text") or "").strip()
    return_mode = request.args.get("return", "json")  # json | file

    if not query:
        return jsonify({"error": "query param is required"}), 400

    api_key = "AIzaSyBmbtPHA8K5ysCrjRHdHMuxqfhxMGNAOsY"
    cx = "154464994ff404d2f"

    def pick_font(font_size: int):
        candidates = [
            "fonts/Roboto-Bold.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
            "/usr/share/fonts/truetype/noto/NotoSans-Bold.ttf",
            "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
            "/Library/Fonts/Arial Unicode.ttf",
            "C:/Windows/Fonts/arial.ttf",
        ]
        last_err = None
        for path in candidates:
            try:
                f = ImageFont.truetype(path, font_size)
                return f
            except Exception as e:
                last_err = e
        raise RuntimeError("Нет TTF с кириллицей. Положи Roboto-Bold.ttf в ./fonts.")

    try:
        # 1) Поиск изображения Google CSE
        url = "https://www.googleapis.com/customsearch/v1"
        params = {"q": query, "cx": cx, "key": api_key, "searchType": "image", "num": 5}
        resp = requests.get(url, params=params, timeout=15)
        data = resp.json() if resp.content else {}

        if resp.status_code == 429:
            return jsonify({"error": "превышен лимит запросов"}), 429

        err = data.get("error")
        if err:
            code = err.get("code")
            status = err.get("status", "")
            reasons = {e.get("reason") for e in err.get("errors", []) if isinstance(e, dict)}
            rate_limited = (
                code == 429
                or status == "RESOURCE_EXHAUSTED"
                or "rateLimitExceeded" in reasons
                or any((d.get("reason") in ("RATE_LIMIT_EXCEEDED", "QUOTA_EXCEEDED"))
                       for d in err.get("details", []) if isinstance(d, dict))
            )
            if rate_limited:
                return jsonify({"error": "превышен лимит запросов"}), 429
            return jsonify({"error": f"google api error: {err.get('message', 'unknown error')}"}), code or 502

        if resp.status_code != 200:
            return jsonify({"error": f"google api http {resp.status_code}"}), resp.status_code

        results = data.get("items", []) or []
        if not results:
            return jsonify({
                "ok": False,
                "error": "no_results",
                "reason": "google_cse_returned_empty_items",
                "image_url": None,
                "overlayed_base64": None
            }), 200

        # 2) Берём первую реально отдающую image/*
        headers = {"User-Agent": "Mozilla/5.0 (compatible; NewsBot/1.0)"}
        image_url, image_data = None, None
        for r in results:
            try:
                candidate = r["link"]
                rimg = requests.get(candidate, headers=headers, timeout=10, verify=False)
                if rimg.status_code == 200 and "image" in rimg.headers.get("Content-Type", ""):
                    image_url, image_data = candidate, rimg.content
                    break
            except Exception:
                continue
        if image_data is None:
            return jsonify({
                "ok": False,
                "error": "no_valid_image",
                "reason": "all_candidate_links_failed_or_not_image_content_type",
                "image_url": None,
                "overlayed_base64": None
            }), 200

        # 3) Ресайз + подложка + текст
        target_size = (1024, 1024)
        base = Image.open(BytesIO(image_data)).convert("RGBA")
        base = ImageOps.fit(base, target_size, method=Image.LANCZOS, centering=(0.5, 0.5))

        font = pick_font(int(target_size[1] * 0.12))
        W, H = target_size
        margin, padding = 40, 28
        line_spacing = max(6, int((getattr(font, "size", 40)) * 0.1))
        max_text_width = W - 2 * margin - 2 * padding

        # перенос по ширине (измерения на временном draw)
        tmp_draw = ImageDraw.Draw(base)
        def wrap_by_width(text, font, max_w, draw):
            words = (text or "").split()
            lines, cur = [], []
            for w in words:
                trial = (' '.join(cur + [w])).strip()
                x0, y0, x1, y1 = draw.textbbox((0, 0), trial, font=font)
                if (x1 - x0) <= max_w:
                    cur.append(w)
                else:
                    if cur:
                        lines.append(' '.join(cur)); cur = [w]
                    else:
                        lines.append(w)
            if cur: lines.append(' '.join(cur))
            return lines

        lines = wrap_by_width(overlay_text, font, max_text_width, tmp_draw)
        try:
            ascent, descent = font.getmetrics()
            line_h = ascent + descent
        except Exception:
            line_h = int(target_size[1] * 0.08)

        line_widths = [(tmp_draw.textbbox((0, 0), ln, font=font)[2]) for ln in lines] if lines else [0]
        content_w = max(line_widths) if line_widths else 0
        content_h = len(lines) * line_h + max(0, len(lines) - 1) * line_spacing

        box_x0 = margin
        box_y0 = H - margin - (content_h + 2 * padding)
        box_x1 = box_x0 + content_w + 2 * padding
        box_y1 = box_y0 + content_h + 2 * padding

        overlay_img = Image.new("RGBA", target_size, (0, 0, 0, 0))
        ImageDraw.Draw(overlay_img).rectangle([box_x0, box_y0, box_x1, box_y1], fill=(0, 0, 0, 160))
        composed = Image.alpha_composite(base, overlay_img)

        # текст рисуем ПОСЛЕ композита
        draw = ImageDraw.Draw(composed)
        tx, ty = box_x0 + padding, box_y0 + padding
        for ln in lines:
            draw.text((tx, ty), ln, font=font, fill=(255, 255, 255, 255))
            ty += line_h + line_spacing

        # 4) Ответ
        if return_mode == "file":
            buf = BytesIO()
            composed.convert("RGB").save(buf, format="JPEG")
            buf.seek(0)
            return send_file(buf, mimetype="image/jpeg", download_name="image.jpg")
        else:
            buf = BytesIO()
            composed.save(buf, format="PNG", optimize=True)
            buf.seek(0)
            encoded = base64.b64encode(buf.getvalue()).decode("utf-8")
            return jsonify({
                "ok": True,
                "image_url": image_url,
                "overlayed_base64": f"data:image/png;base64,{encoded}"
            })

    except requests.Timeout:
        return jsonify({"error": "превышено время ожидания запроса к Google"}), 504
    except Exception as e:
        return jsonify({"error": str(e)}), 500




import os

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))  # Берём порт из окружения
    app.run(host="0.0.0.0", port=port)
