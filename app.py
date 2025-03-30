from flask import Flask, render_template, request, jsonify
import pandas as pd
import numpy as np
from tvDatafeed import TvDatafeed, Interval
import schedule
import time
import threading
from scipy.signal import argrelextrema

app = Flask(__name__)

# Khởi tạo TvDatafeed
tv = TvDatafeed()

# Danh sách coin mặc định và biến lưu trữ
default_coins = [
    {"symbol": "BTCUSDT", "exchange": "BINANCE"},
    {"symbol": "ETHUSDT", "exchange": "BINANCE"}
]
tracked_coins = default_coins.copy()
analysis_cache = {"results": [], "breakout_status": {}, "last_updated": None}

# Hàm nhận diện mẫu nến
def detect_candlestick_pattern(df):
    last_candle = df.iloc[-1]
    open_price = last_candle['open']
    close_price = last_candle['close']
    high_price = last_candle['high']
    low_price = last_candle['low']
    body_size = abs(close_price - open_price)
    upper_shadow = high_price - max(open_price, close_price)
    lower_shadow = min(open_price, close_price) - low_price

    if body_size < (high_price - low_price) * 0.1:
        return "Doji"
    elif lower_shadow > body_size * 2 and upper_shadow < body_size * 0.5 and close_price > open_price:
        return "Hammer"
    elif len(df) > 1 and df['close'].iloc[-2] > df['open'].iloc[-2] and close_price < open_price and close_price < df['open'].iloc[-2] and open_price > df['close'].iloc[-2]:
        return "Bearish Engulfing"
    elif len(df) > 1 and df['close'].iloc[-2] < df['open'].iloc[-2] and close_price > open_price and close_price > df['open'].iloc[-2] and open_price < df['close'].iloc[-2]:
        return "Bullish Engulfing"
    else:
        return "N/A"

# Hàm nhận diện mẫu hình giá
def detect_price_pattern(df):
    maxima = argrelextrema(df['high'].values, np.greater, order=5)[0]
    minima = argrelextrema(df['low'].values, np.less, order=5)[0]
    highs = df['high'].iloc[maxima].tolist()
    lows = df['low'].iloc[minima].tolist()

    if len(highs) < 2 or len(lows) < 2:
        return "N/A", 0

    if len(highs) >= 2 and abs(highs[-1] - highs[-2]) / highs[-1] < 0.02:
        similarity = (1 - abs(highs[-1] - highs[-2]) / highs[-1]) * 100
        return "Double Top", similarity
    elif len(lows) >= 2 and abs(lows[-1] - lows[-2]) / lows[-1] < 0.02:
        similarity = (1 - abs(lows[-1] - lows[-2]) / lows[-1]) * 100
        return "Double Bottom", similarity
    else:
        return "N/A", 0

# Hàm xác định xu hướng
def determine_trend(df):
    maxima = argrelextrema(df['high'].values, np.greater, order=5)[0][-4:]
    minima = argrelextrema(df['low'].values, np.less, order=5)[0][-4:]
    if len(maxima) < 2 or len(minima) < 2:
        return "Đi ngang"

    high_trend = np.mean(df['high'].iloc[maxima[-2:]].values) - np.mean(df['high'].iloc[maxima[:2]].values)
    low_trend = np.mean(df['low'].iloc[minima[-2:]].valuesstatic/images/icon-error.png)low_trend = np.mean(df['low'].iloc[minima[-2:]].values) - np.mean(df['low'].iloc[minima[:2]].values)
    
    if high_trend > 0 and low_trend > 0:
        return "Tăng"
    elif high_trend < 0 and low_trend < 0:
        return "Giảm"
    else:
        return "Đi ngang"

# Hàm phân tích hàng ngày
def daily_analysis():
    global analysis_cache
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

            breakout_status[symbol] = {"support": current_price < nearest_support if nearest_support != 'N/A' else False,
                                      "resistance": current_price > nearest_resistance if nearest_resistance != 'N/A' else False,
                                      "message": ""}
            if breakout_status[symbol]["support"]:
                breakout_status[symbol]["message"] = f"CẢNH BÁO: {symbol} đã phá vỡ vùng hỗ trợ {nearest_support}!"
            elif breakout_status[symbol]["resistance"]:
                breakout_status[symbol]["message"] = f"CẢNH BÁO: {symbol} đã phá vỡ vùng kháng cự {nearest_resistance}!"

            candlestick_pattern = detect_candlestick_pattern(df_4h)
            price_pattern, similarity = detect_price_pattern(df_daily)
            trend = determine_trend(df_daily)

            result = (f"{symbol}:\n"
                      f"Giá hiện tại: {current_price:.2f}\n"
                      f"Hỗ trợ gần nhất: {nearest_support}, Kháng cự gần nhất: {nearest_resistance}\n"
                      f"Hỗ trợ mạnh: {strong_support}, Kháng cự mạnh: {strong_resistance}\n"
                      f"Mẫu nến 4H: {candlestick_pattern}\n"
                      f"Mẫu hình giá 1D: {price_pattern} ({similarity:.0f}% tương đồng)\n"
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
        except:
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
        except:
            changes[coin["symbol"]] = None
    return jsonify(changes)

if __name__ == '__main__':
    daily_analysis()  # Chạy phân tích lần đầu khi khởi động
    app.run(debug=True)