from flask import Flask, render_template, request, jsonify, send_from_directory
import pandas as pd
import numpy as np
from tvDatafeed import TvDatafeed, Interval
import schedule
import time
import threading
from scipy.signal import argrelextrema
from sklearn.ensemble import RandomForestClassifier
from sklearn.preprocessing import StandardScaler
import requests
import feedparser
import logging
from bs4 import BeautifulSoup
from datetime import datetime
from pywebpush import webpush
import json
import os
from werkzeug.utils import secure_filename

app = Flask(__name__)

# Thiết lập logging
logging.basicConfig(level=logging.DEBUG, format='%(asctime)s - %(levelname)s - %(message)s')

# Khởi tạo TvDatafeed
tv = TvDatafeed()

# API Key cho NewsAPI
NEWS_API_KEY = "e8dea5c6f2894640ba6676a7d7b37943"

# VAPID Keys (Đã chuyển đổi từ PEM sang base64 URL-safe)
VAPID_PUBLIC_KEY = "BCZw7Kj8eL3mK5pXzJ9b8fQ7kZ2mXz5n8g9jQw8fK5vL8mN7pXzJ9b8fQ7kZ2mXz5n8g9jQw8fK5vL8mN7pXzJ9b8"
VAPID_PRIVATE_KEY = "RPnOralZzMuZ8nA9TTsw08a7WzTWXx_iJ6ubxYgoqy0"
VAPID_CLAIMS = {"sub": "mailto:csgtdu@gmail.com"}

# RSS Feeds cho tin tức Crypto
RSS_FEEDS = {
    "crypto": {
        "CoinDesk": "https://www.coindesk.com/feed",
        "CoinTelegraph": "https://cointelegraph.com/rss",
        "The Block": "https://www.theblock.co/feed"
    }
}

# Danh sách coin mặc định
default_coins = [
    {"symbol": "BTCUSDT", "exchange": "BINANCE"},
    {"symbol": "ETHUSDT", "exchange": "BINANCE"},
    {"symbol": "ALPHUSDT", "exchange": "MEXC"},
    {"symbol": "KASUSDT", "exchange": "MEXC"}
]
tracked_coins = default_coins.copy()
analysis_cache = {"results": [], "breakout_status": {}, "last_updated": None}
news_cache = {"crypto": [], "economics": [], "sports": [], "last_updated": None}

# Thư mục lưu logo
UPLOAD_FOLDER = 'static/uploads'
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg'}

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

# Huấn luyện mô hình Machine Learning
def train_price_pattern_model():
    X = np.array([
        [0.02, 0.01, 0.015, 0.5],  # Double Top
        [0.01, 0.02, -0.01, -0.3], # Double Bottom
        [0.03, 0.02, 0.01, 0.7],   # Head and Shoulders
    ])
    y = ["Double Top", "Double Bottom", "Head and Shoulders"]
    scaler = StandardScaler()
    X_scaled = scaler.fit_transform(X)
    model = RandomForestClassifier(n_estimators=100, random_state=42)
    model.fit(X_scaled, y)
    return model, scaler

ml_model, scaler = train_price_pattern_model()

# Hàm nhận diện mẫu nến
def detect_candlestick_pattern(df):
    if len(df) < 2:
        return "N/A"
    last_candle = df.iloc[-1]
    prev_candle = df.iloc[-2]
    o, c, h, l = last_candle['open'], last_candle['close'], last_candle['high'], last_candle['low']
    po, pc = prev_candle['open'], prev_candle['close']
    body_size = abs(c - o)
    range_size = h - l
    upper_shadow = h - max(o, c)
    lower_shadow = min(o, c) - l

    if body_size < range_size * 0.1:
        return "Doji"
    elif lower_shadow > body_size * 2 and upper_shadow < body_size * 0.5 and c > o:
        return "Hammer"
    elif upper_shadow > body_size * 2 and lower_shadow < body_size * 0.5 and c < o:
        return "Shooting Star"
    else:
        return "N/A"

# Hàm nhận diện mẫu hình giá
def detect_price_pattern(df):
    maxima = argrelextrema(df['high'].values, np.greater, order=5)[0]
    minima = argrelextrema(df['low'].values, np.less, order=5)[0]
    highs = df['high'].iloc[maxima].tolist()
    lows = df['low'].iloc[minima].tolist()

    if len(highs) < 2 or len(lows) < 2:
        return "N/A", 0, "Không xác định"

    high_diff = abs(highs[-1] - highs[-2]) / highs[-1] if len(highs) >= 2 else 0
    low_diff = abs(lows[-1] - lows[-2]) / lows[-1] if len(lows) >= 2 else 0
    trend_high = (highs[-1] - highs[-2]) / highs[-2] if len(highs) >= 2 else 0
    volatility = df['high'].std() / df['high'].mean()
    features = np.array([[high_diff, low_diff, trend_high, volatility]])
    features_scaled = scaler.transform(features)

    ml_prediction = ml_model.predict(features_scaled)[0]
    ml_confidence = max(ml_model.predict_proba(features_scaled)[0]) * 100

    patterns = {
        "Double Top": (len(highs) >= 2 and abs(highs[-1] - highs[-2]) / highs[-1] < 0.02, "Đỉnh Đôi"),
        "Double Bottom": (len(lows) >= 2 and abs(lows[-1] - lows[-2]) / lows[-1] < 0.02, "Đáy Đôi"),
    }
    for pattern, (condition, vn_name) in patterns.items():
        if condition:
            return pattern, 90, vn_name
    return ml_prediction, ml_confidence, "Không xác định"

# Hàm xác định xu hướng
def determine_trend(df):
    maxima = argrelextrema(df['high'].values, np.greater, order=5)[0][-4:]
    minima = argrelextrema(df['low'].values, np.less, order=5)[0][-4:]
    if len(maxima) < 2 or len(minima) < 2:
        return "Đi ngang"

    high_trend = np.mean(df['high'].iloc[maxima[-2:]].values) - np.mean(df['high'].iloc[maxima[:2]].values)
    low_trend = np.mean(df['low'].iloc[minima[-2:]].values) - np.mean(df['low'].iloc[minima[:2]].values)
    
    if high_trend > 0 and low_trend > 0:
        return "Tăng"
    elif high_trend < 0 and low_trend < 0:
        return "Giảm"
    else:
        return "Đi ngang"

# Hàm phân tích hàng ngày
def daily_analysis():
    global analysis_cache
    analysis_cache = {"results": [], "breakout_status": {}, "last_updated": None}
    results = []
    breakout_status = {}
    for coin in tracked_coins:
        symbol = coin["symbol"]
        exchange = coin["exchange"]
        try:
            df_daily = tv.get_hist(symbol=symbol, exchange=exchange, interval=Interval.in_daily, n_bars=100)
            df_4h = tv.get_hist(symbol=symbol, exchange=exchange, interval=Interval.in_4_hour, n_bars=100)

            if df_daily.empty or df_4h.empty:
                results.append(f"{symbol}: Không có dữ liệu\n")
                continue

            current_price = df_daily['close'].iloc[-1]
            maxima = argrelextrema(df_daily['high'].values, np.greater, order=5)[0]
            minima = argrelextrema(df_daily['low'].values, np.less, order=5)[0]
            resistance_levels = df_daily['high'].iloc[maxima].tolist()
            support_levels = df_daily['low'].iloc[minima].tolist()

            nearest_resistance = min([r for r in resistance_levels if r > current_price], default='N/A')
            nearest_support = max([s for s in support_levels if s < current_price], default='N/A')
            strong_resistance = max(resistance_levels, default='N/A')
            strong_support = min(support_levels, default='N/A')

            breakout_status[symbol] = {
                "support": current_price < nearest_support if nearest_support != 'N/A' else False,
                "resistance": current_price > nearest_resistance if nearest_resistance != 'N/A' else False,
                "message": ""
            }
            if breakout_status[symbol]["support"]:
                breakout_status[symbol]["message"] = f"CẢNH BÁO: {symbol} đã phá vỡ vùng hỗ trợ {nearest_support}!"
            elif breakout_status[symbol]["resistance"]:
                breakout_status[symbol]["message"] = f"CẢNH BÁO: {symbol} đã phá vỡ vùng kháng cự {nearest_resistance}!"

            candlestick_pattern = detect_candlestick_pattern(df_4h)
            price_pattern, similarity, vn_pattern = detect_price_pattern(df_daily)
            trend = determine_trend(df_daily)

            result = (f"{symbol}:\n"
                      f"Giá hiện tại: {current_price:.2f}\n"
                      f"Hỗ trợ gần nhất: {nearest_support}, Kháng cự gần nhất: {nearest_resistance}\n"
                      f"Hỗ trợ mạnh: {strong_support}, Kháng cự mạnh: {strong_resistance}\n"
                      f"Mẫu nến 4H: {candlestick_pattern}\n"
                      f"Mẫu hình giá 1D: {price_pattern} ({vn_pattern}) - {similarity:.0f}%\n"
                      f"Hợp lưu: N/A\n"
                      f"Xu hướng chung: {trend}\n")
            results.append(result)
        except Exception as e:
            results.append(f"{symbol}: Lỗi khi phân tích - {str(e)}\n")

    analysis_cache = {
        "results": results,
        "breakout_status": breakout_status,
        "last_updated": time.strftime("%Y-%m-%d %H:%M:%S")
    }

# Hàm lấy nội dung bài viết và tóm tắt
def summarize_article(url):
    try:
        response = requests.get(url, timeout=10)
        soup = BeautifulSoup(response.content, 'html.parser')
        paragraphs = soup.find_all('p')
        text = ' '.join(p.get_text() for p in paragraphs[:5])
        words = text.split()
        if len(words) > 200:
            text = ' '.join(words[:200]) + '...'
        return text
    except Exception as e:
        logging.error(f"Error summarizing {url}: {str(e)}")
        return "Không thể tóm tắt bài viết này."

# Hàm lấy tin tức
def fetch_news():
    global news_cache
    news = {"crypto": [], "economics": [], "sports": []}
    
    for source, url in RSS_FEEDS["crypto"].items():
        try:
            feed = feedparser.parse(url)
            for entry in feed.entries[:5]:
                summary = summarize_article(entry.link)
                news["crypto"].append({
                    "title": entry.title,
                    "url": entry.link,
                    "summary": summary
                })
        except Exception as e:
            logging.error(f"Error parsing RSS {source}: {str(e)}")

    url = f"https://newsapi.org/v2/top-headlines?category=business&language=en&apiKey={NEWS_API_KEY}"
    try:
        response = requests.get(url)
        data = response.json()
        for article in data["articles"][:5]:
            summary = summarize_article(article["url"])
            news["economics"].append({
                "title": article["title"],
                "url": article["url"],
                "summary": summary
            })
    except Exception as e:
        logging.error(f"Error fetching NewsAPI economics: {str(e)}")

    news["sports"] = [{"title": "Chưa có tin tức", "url": "#", "summary": "Chưa có dữ liệu."}]

    news_cache = {
        "crypto": news["crypto"],
        "economics": news["economics"],
        "sports": news["sports"],
        "last_updated": time.strftime("%Y-%m-%d %H:%M:%S")
    }

# Lên lịch
schedule.every().day.at("18:00").do(fetch_news)
schedule.every(1).hours.do(daily_analysis)

def run_schedule():
    while True:
        schedule.run_pending()
        time.sleep(60)

threading.Thread(target=run_schedule, daemon=True).start()

# Endpoint chính
@app.route('/')
def index():
    return render_template('index.html', analysis_results=analysis_cache["results"],
                          BREAKOUT_STATUS=analysis_cache["breakout_status"],
                          last_updated=analysis_cache["last_updated"],
                          news_cache=news_cache,
                          vapid_public_key=VAPID_PUBLIC_KEY,
                          tracked_coins=tracked_coins)

# Endpoint phân tích
@app.route('/analyze', methods=['GET'])
def analyze():
    daily_analysis()
    return "Phân tích hoàn tất"

# Endpoint thêm coin
@app.route('/add_coin', methods=['POST'])
def add_coin():
    symbol = request.form.get('symbol') + "USDT"
    exchange = request.form.get('exchange')
    new_coin = {"symbol": symbol, "exchange": exchange}
    if new_coin not in tracked_coins:
        tracked_coins.append(new_coin)
        daily_analysis()
        return jsonify({"status": "success", "message": f"Đã thêm {symbol} vào danh sách theo dõi!"})
    return jsonify({"status": "error", "message": f"{symbol} đã có trong danh sách theo dõi!"})

# Endpoint xóa coin
@app.route('/remove_coin', methods=['POST'])
def remove_coin():
    symbol = request.form.get('symbol')
    global tracked_coins
    tracked_coins = [coin for coin in tracked_coins if coin["symbol"] != symbol]
    daily_analysis()
    return jsonify({"status": "success", "message": f"Đã xóa {symbol} khỏi danh sách theo dõi!"})

# Endpoint giá hiện tại
@app.route('/get_prices', methods=['GET'])
def get_prices():
    prices = {}
    for coin in tracked_coins:
        try:
            df = tv.get_hist(symbol=coin["symbol"], exchange=coin["exchange"], interval=Interval.in_1_minute, n_bars=1)
            prices[coin["symbol"]] = df['close'].iloc[-1]
        except Exception as e:
            prices[coin["symbol"]] = None
    return jsonify(prices)

# Endpoint biến động 24h
@app.route('/get_price_change_24h', methods=['GET'])
def get_price_change_24h():
    changes = {}
    for coin in tracked_coins:
        try:
            df = tv.get_hist(symbol=coin["symbol"], exchange=coin["exchange"], interval=Interval.in_1_hour, n_bars=24)
            price_change = ((df['close'].iloc[-1] - df['close'].iloc[0]) / df['close'].iloc[0]) * 100
            changes[coin["symbol"]] = price_change
        except Exception as e:
            changes[coin["symbol"]] = None
    return jsonify(changes)

# Endpoint tin tức
@app.route('/news/<category>', methods=['GET'])
def get_news(category):
    if category in news_cache and news_cache[category]:
        return jsonify(news_cache[category])
    return jsonify([])

# Endpoint gửi thông báo đẩy
@app.route('/push', methods=['POST'])
def send_push():
    data = request.get_json()
    subscription = data['subscription']
    message = data['message']
    try:
        logging.info(f"Sending push to {subscription['endpoint']}")
        webpush(
            subscription_info=subscription,
            data=json.dumps({"title": "BOT Trading", "body": message}),
            vapid_private_key=VAPID_PRIVATE_KEY,
            vapid_claims=VAPID_CLAIMS
        )
        logging.info("Push sent successfully")
        return jsonify({"status": "success"})
    except Exception as e:
        logging.error(f"Push error: {str(e)}")
        return jsonify({"status": "error", "message": str(e)})

# Endpoint upload logo
@app.route('/upload_logo', methods=['POST'])
def upload_logo():
    if 'logo' not in request.files:
        return jsonify({"status": "error", "message": "Không có file được tải lên!"})
    file = request.files['logo']
    if file.filename == '':
        return jsonify({"status": "error", "message": "Chưa chọn file!"})
    if file and allowed_file(file.filename):
        filename = secure_filename(file.filename)
        file.save(os.path.join(app.config['UPLOAD_FOLDER'], 'custom_logo.png'))
        return jsonify({"status": "success", "message": "Logo đã được tải lên!"})
    return jsonify({"status": "error", "message": "File không hợp lệ!"})

# Phục vụ Service Worker
@app.route('/sw.js')
def serve_sw():
    return send_from_directory('static', 'sw.js')

if __name__ == '__main__':
    os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
    daily_analysis()
    fetch_news()
    app.run(debug=True)