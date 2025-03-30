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

# Hàm phân tích hàng ngày
def daily_analysis():
    global analysis_cache
    results = []
    breakout_status = {}
    for coin in tracked_coins:
        symbol = coin["symbol"]
        exchange = coin["exchange"]
        try:
            # Lấy dữ liệu ngày
            df_daily = tv.get_hist(symbol=symbol, exchange=exchange, interval=Interval.in_daily, n_bars=100)
            df_4h = tv.get_hist(symbol=symbol, exchange=exchange, interval=Interval.in_4_hour, n_bars=100)

            if df_daily.empty or df_4h.empty:
                results.append(f"{symbol}: Không có dữ liệu\n")
                continue

            # Giá hiện tại
            current_price = df_daily['close'].iloc[-1]

            # Tính mức hỗ trợ và kháng cự
            maxima = argrelextrema(df_daily['high'].values, np.greater, order=5)[0]
            minima = argrelextrema(df_daily['low'].values, np.less, order=5)[0]
            resistance_levels = df_daily['high'].iloc[maxima].tolist()
            support_levels = df_daily['low'].iloc[minima].tolist()

            nearest_resistance = min([r for r in resistance_levels if r > current_price], default='N/A')
            nearest_support = max([s for s in support_levels if s < current_price], default='N/A')
            strong_resistance = max(resistance_levels, default='N/A')
            strong_support = min(support_levels, default='N/A')

            # Kiểm tra breakout
            breakout_status[symbol] = {"support": current_price < nearest_support if nearest_support != 'N/A' else False,
                                      "resistance": current_price > nearest_resistance if nearest_resistance != 'N/A' else False,
                                      "message": ""}
            if breakout_status[symbol]["support"]:
                breakout_status[symbol]["message"] = f"CẢNH BÁO: {symbol} đã phá vỡ vùng hỗ trợ {nearest_support}!"
            elif breakout_status[symbol]["resistance"]:
                breakout_status[symbol]["message"] = f"CẢNH BÁO: {symbol} đã phá vỡ vùng kháng cự {nearest_resistance}!"

            # Xu hướng chung
            trend = "Tăng" if df_daily['close'].iloc[-1] > df_daily['close'].iloc[-2] else "Giảm" if df_daily['close'].iloc[-1] < df_daily['close'].iloc[-2] else "Đi ngang"

            # Kết quả phân tích
            result = (f"{symbol}:\n"
                      f"Giá hiện tại: {current_price:.2f}\n"
                      f"Hỗ trợ gần nhất: {nearest_support}, Kháng cự gần nhất: {nearest_resistance}\n"
                      f"Hỗ trợ mạnh: {strong_support}, Kháng cự mạnh: {strong_resistance}\n"
                      f"Mẫu nến 4H: N/A\n"
                      f"Mẫu hình giá 1D: N/A (0%)\n"
                      f"Hợp lưu: N/A\n"
                      f"Xu hướng chung: {trend}\n")
            results.append(result)
        except Exception as e:
            results.append(f"{symbol}: Lỗi khi phân tích - {str(e)}\n")

    # Cập nhật cache và xóa dữ liệu cũ
    analysis_cache = {
        "results": results,
        "breakout_status": breakout_status,
        "last_updated": time.strftime("%Y-%m-%d %H:%M:%S")
    }

# Chạy phân tích định kỳ mỗi 4 giờ
def run_schedule():
    while True:
        schedule.run_pending()
        time.sleep(1)

schedule.every(4).hours.do(daily_analysis)
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
        daily_analysis()  # Cập nhật phân tích ngay khi thêm coin
        return jsonify({"status": "success", "message": f"Đã thêm {symbol} vào danh sách theo dõi!"})
    return jsonify({"status": "error", "message": f"{symbol} đã có trong danh sách theo dõi!"})

# Endpoint xóa coin
@app.route('/remove_coin', methods=['POST'])
def remove_coin():
    symbol = request.form.get('symbol')
    global tracked_coins
    tracked_coins = [coin for coin in tracked_coins if coin["symbol"] != symbol]
    daily_analysis()  # Cập nhật phân tích sau khi xóa
    return jsonify({"status": "success", "message": f"Đã xóa {symbol} khỏi danh sách theo dõi!"})

# Endpoint reset danh sách coin
@app.route('/reset_coins', methods=['POST'])
def reset_coins():
    global tracked_coins
    tracked_coins = default_coins.copy()
    daily_analysis()
    return jsonify({"status": "success", "message": "Đã reset danh sách coin!"})

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