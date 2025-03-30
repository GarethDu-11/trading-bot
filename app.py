from flask import Flask, render_template, request, jsonify
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

app = Flask(__name__)

# Thiết lập logging
logging.basicConfig(level=logging.DEBUG, format='%(asctime)s - %(levelname)s - %(message)s')

# Khởi tạo TvDatafeed
tv = TvDatafeed()

# API Key cho NewsAPI (nên thay bằng biến môi trường trong thực tế)
NEWS_API_KEY = "e8dea5c6f2894640ba6676a7d7b37943"

# RSS Feeds cho tin tức Crypto
RSS_FEEDS = {
    "crypto": {
        "CoinDesk": "https://www.coindesk.com/feed",
        "CoinTelegraph": "https://cointelegraph.com/rss",
        "The Block": "https://www.theblock.co/feed"
    }
}

# Danh sách coin mặc định và biến lưu trữ
default_coins = [
    {"symbol": "BTCUSDT", "exchange": "BINANCE"},
    {"symbol": "ETHUSDT", "exchange": "BINANCE"},
    {"symbol": "ALPHUSDT", "exchange": "MEXC"},
    {"symbol": "KASUSDT", "exchange": "MEXC"}
]
tracked_coins = default_coins.copy()
analysis_cache = {"results": [], "breakout_status": {}, "last_updated": None}

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
    elif pc < po and c > o and c > po and o < pc:
        return "Bullish Engulfing"
    elif pc > po and c < o and c < po and o > pc:
        return "Bearish Engulfing"
    elif pc < po and c > o and o > pc and c < po:
        return "Bullish Harami"
    elif pc > po and c < o and o < pc and c > po:
        return "Bearish Harami"
    if len(df) > 2 and df['close'].iloc[-3] > df['open'].iloc[-3] and pc < po and c > o and c > (df['close'].iloc[-3] + po) / 2:
        return "Morning Star"
    elif len(df) > 2 and df['close'].iloc[-3] < df['open'].iloc[-3] and pc > po and c < o and c < (df['close'].iloc[-3] + po) / 2:
        return "Evening Star"
    elif lower_shadow > body_size * 2 and upper_shadow < body_size * 0.5 and c < o:
        return "Hanging Man"
    elif upper_shadow > body_size * 2 and lower_shadow < body_size * 0.5 and c > o:
        return "Inverted Hammer"
    else:
        return "N/A"

# Hàm nhận diện mẫu hình giá với Machine Learning
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
        "Triple Top": (len(highs) >= 3 and max([abs(highs[i] - highs[j]) for i, j in [(0, 1), (1, 2), (0, 2)]]) / max(highs) < 0.03, "Đỉnh Ba"),
        "Triple Bottom": (len(lows) >= 3 and max([abs(lows[i] - lows[j]) for i, j in [(0, 1), (1, 2), (0, 2)]]) / max(lows) < 0.03, "Đáy Ba"),
        "Head and Shoulders": (len(highs) >= 3 and highs[-2] > highs[-1] and highs[-2] > highs[-3] and abs(highs[-1] - highs[-3]) / highs[-2] < 0.05, "Đầu và Vai"),
        "Inverse Head and Shoulders": (len(lows) >= 3 and lows[-2] < lows[-1] and lows[-2] < lows[-3] and abs(lows[-1] - lows[-3]) / lows[-2] < 0.05, "Đầu và Vai Ngược"),
        "Rising Wedge": (len(highs) >= 3 and len(lows) >= 3 and highs[-1] > highs[-2] > highs[-3] and lows[-1] > lows[-2] > lows[-3] and (highs[-1] - lows[-1]) < (highs[-3] - lows[-3]), "Nêm Tăng"),
        "Falling Wedge": (len(highs) >= 3 and len(lows) >= 3 and highs[-1] < highs[-2] < highs[-3] and lows[-1] < lows[-2] < lows[-3] and (highs[-1] - lows[-1]) > (highs[-3] - lows[-3]), "Nêm Giảm"),
        "Bullish Reversal": (len(lows) >= 2 and lows[-1] > lows[-2] and df['close'].iloc[-1] > df['open'].iloc[-1], "Đảo Chiều Tăng"),
        "Bearish Reversal": (len(highs) >= 2 and highs[-1] < highs[-2] and df['close'].iloc[-1] < df['open'].iloc[-1], "Đảo Chiều Giảm"),
        "Bullish Flag": (len(highs) >= 2 and len(lows) >= 2 and highs[-1] < highs[-2] and lows[-1] > lows[-2] and df['close'].iloc[-1] > df['close'].iloc[-2], "Cờ Tăng"),
        "Bearish Flag": (len(highs) >= 2 and len(lows) >= 2 and highs[-1] > highs[-2] and lows[-1] < lows[-2] and df['close'].iloc[-1] < df['close'].iloc[-2], "Cờ Giảm"),
        "Symmetrical Triangle": (len(highs) >= 3 and len(lows) >= 3 and highs[-1] < highs[-2] and lows[-1] > lows[-2] and (highs[-1] - lows[-1]) < (highs[-2] - lows[-2]), "Tam Giác Đối Xứng"),
        "Ascending Triangle": (len(highs) >= 3 and len(lows) >= 3 and abs(highs[-1] - highs[-2]) / highs[-1] < 0.02 and lows[-1] > lows[-2], "Tam Giác Tăng"),
        "Descending Triangle": (len(highs) >= 3 and len(lows) >= 3 and highs[-1] < highs[-2] and abs(lows[-1] - lows[-2]) / lows[-1] < 0.02, "Tam Giác Giảm"),
        "Bullish Pennant": (len(highs) >= 3 and len(lows) >= 3 and highs[-1] < highs[-2] and lows[-1] > lows[-2] and (highs[-1] - lows[-1]) < (highs[-2] - lows[-2]) and df['close'].iloc[-1] > df['close'].iloc[-2], "Cờ Đuôi Nheo Tăng"),
        "Bearish Pennant": (len(highs) >= 3 and len(lows) >= 3 and highs[-1] > highs[-2] and lows[-1] < lows[-2] and (highs[-1] - lows[-1]) < (highs[-2] - lows[-2]) and df['close'].iloc[-1] < df['close'].iloc[-2], "Cờ Đuôi Nheo Giảm"),
        "Rectangle": (len(highs) >= 2 and len(lows) >= 2 and abs(highs[-1] - highs[-2]) / highs[-1] < 0.02 and abs(lows[-1] - lows[-2]) / lows[-1] < 0.02, "Hình Chữ Nhật"),
        "Channel Up": (len(highs) >= 3 and len(lows) >= 3 and highs[-1] > highs[-2] > highs[-3] and lows[-1] > lows[-2] > lows[-3], "Kênh Tăng"),
        "Channel Down": (len(highs) >= 3 and len(lows) >= 3 and highs[-1] < highs[-2] < highs[-3] and lows[-1] < lows[-2] < lows[-3], "Kênh Giảm"),
        "Cup and Handle": (len(lows) >= 3 and lows[-2] < lows[-1] and lows[-2] < lows[-3] and highs[-1] < highs[-2], "Cốc và Tay Cầm"),
        "Inverse Cup and Handle": (len(highs) >= 3 and highs[-2] > highs[-1] and highs[-2] > highs[-3] and lows[-1] > lows[-2], "Cốc và Tay Cầm Ngược"),
        "Rounding Bottom": (len(lows) >= 3 and lows[-1] > lows[-2] and lows[-2] < lows[-3], "Đáy Tròn"),
        "Rounding Top": (len(highs) >= 3 and highs[-1] < highs[-2] and highs[-2] > highs[-3], "Đỉnh Tròn"),
        "V Top": (len(highs) >= 2 and len(lows) >= 2 and highs[-1] < highs[-2] and lows[-1] > lows[-2], "Đỉnh Chữ V"),
        "V Bottom": (len(highs) >= 2 and len(lows) >= 2 and highs[-1] > highs[-2] and lows[-1] < lows[-2], "Đáy Chữ V"),
        "Spike Top": (len(highs) >= 2 and highs[-1] > highs[-2] * 1.1, "Đỉnh Nhọn"),
        "Spike Bottom": (len(lows) >= 2 and lows[-1] < lows[-2] * 0.9, "Đáy Nhọn"),
        "Broadening Top": (len(highs) >= 3 and highs[-1] > highs[-2] and highs[-2] < highs[-3] and lows[-1] < lows[-2], "Đỉnh Mở Rộng"),
        "Broadening Bottom": (len(lows) >= 3 and lows[-1] < lows[-2] and lows[-2] > lows[-3] and highs[-1] > highs[-2], "Đáy Mở Rộng")
    }

    for pattern, (condition, vn_name) in patterns.items():
        if condition:
            if pattern == ml_prediction:
                return pattern, max(ml_confidence, 90), vn_name
            return pattern, 90, vn_name
    return ml_prediction, ml_confidence, "Dự đoán ML" if ml_prediction != "N/A" else "Không xác định"

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
                      f"Mẫu hình giá 1D: {price_pattern} ({vn_pattern}) - {similarity:.0f}% tương đồng\n"
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

# Hàm lấy tin tức từ NewsAPI (Bloomberg, Reuters)
def get_newsapi_articles(category):
    url = f"https://newsapi.org/v2/top-headlines?sources={category}&apiKey={NEWS_API_KEY}"
    try:
        response = requests.get(url)
        response.raise_for_status()
        data = response.json()
        articles = [{"title": article["title"], "url": article["url"]} for article in data["articles"][:5]]
        logging.debug(f"Fetched {len(articles)} articles from NewsAPI for {category}")
        return articles
    except Exception as e:
        logging.error(f"Error fetching NewsAPI for {category}: {str(e)}")
        return []

# Hàm lấy tin tức từ RSS
def get_rss_articles(feed_url):
    try:
        feed = feedparser.parse(feed_url)
        articles = [{"title": entry.title, "url": entry.link} for entry in feed.entries[:5]]
        logging.debug(f"Parsed {len(articles)} articles from RSS feed {feed_url}")
        return articles
    except Exception as e:
        logging.error(f"Error parsing RSS feed {feed_url}: {str(e)}")
        return []

# Chạy phân tích định kỳ mỗi 1 giờ
def run_schedule():
    while True:
        schedule.run_pending()
        time.sleep(1)

schedule.every(1).hours.do(daily_analysis)
threading.Thread(target=run_schedule, daemon=True).start()

# Endpoint chính
@app.route('/')
def index():
    return render_template('index.html', analysis_results=analysis_cache["results"],
                          BREAKOUT_STATUS=analysis_cache["breakout_status"],
                          last_updated=analysis_cache["last_updated"])

# Endpoint phân tích thủ công
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

# Endpoint lấy giá hiện tại
@app.route('/get_prices', methods=['GET'])
def get_prices():
    prices = {}
    for coin in tracked_coins:
        try:
            df = tv.get_hist(symbol=coin["symbol"], exchange=coin["exchange"], interval=Interval.in_1_minute, n_bars=1)
            prices[coin["symbol"]] = df['close'].iloc[-1]
        except Exception as e:
            logging.error(f"Error fetching price for {coin['symbol']}: {str(e)}")
            prices[coin["symbol"]] = None
    return jsonify(prices)

# Endpoint lấy biến động 24h
@app.route('/get_price_change_24h', methods=['GET'])
def get_price_change_24h():
    changes = {}
    for coin in tracked_coins:
        try:
            df = tv.get_hist(symbol=coin["symbol"], exchange=coin["exchange"], interval=Interval.in_1_hour, n_bars=24)
            price_change = ((df['close'].iloc[-1] - df['close'].iloc[0]) / df['close'].iloc[0]) * 100
            changes[coin["symbol"]] = price_change
        except Exception as e:
            logging.error(f"Error fetching 24h change for {coin['symbol']}: {str(e)}")
            changes[coin["symbol"]] = None
    return jsonify(changes)

# Endpoint lấy tin tức
@app.route('/news/<category>', methods=['GET'])
def get_news(category):
    if category == "crypto":
        news = []
        for source, url in RSS_FEEDS["crypto"].items():
            articles = get_rss_articles(url)
            news.extend(articles)
        return jsonify(news[:15])  # Giới hạn 15 tin
    elif category == "economics":
        articles = get_newsapi_articles("bloomberg,reuters")
        return jsonify(articles)
    elif category == "sports":
        return jsonify([])  # Chưa có nguồn, trả về rỗng
    else:
        logging.warning(f"Unknown category requested: {category}")
        return jsonify([])

if __name__ == '__main__':
    daily_analysis()
    app.run(debug=True)