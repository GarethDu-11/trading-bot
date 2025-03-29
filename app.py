import pandas as pd
import schedule
import time
import logging
import json
import os
from threading import Thread
from flask import Flask, render_template, jsonify, request
from tvDatafeed import TvDatafeed, Interval
from difflib import SequenceMatcher

# Thiết lập logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

app = Flask(__name__, template_folder='templates')

# Đường dẫn đến file JSON lưu danh sách coin
COINS_FILE = "coins.json"

# Đọc danh sách coin từ file JSON hoặc khởi tạo nếu không tồn tại
def load_coins():
    if os.path.exists(COINS_FILE):
        with open(COINS_FILE, 'r') as f:
            coins = json.load(f)
            logger.info(f"Đã tải {len(coins)} coin từ {COINS_FILE}")
            return coins
    else:
        default_coins = [
            {"symbol": "BTCUSDT", "tv_symbol": "BTCUSDT", "exchange": "BINANCE"},
            {"symbol": "ETHUSDT", "tv_symbol": "ETHUSDT", "exchange": "BINANCE"},
            {"symbol": "XRPUSDT", "tv_symbol": "XRPUSDT", "exchange": "BINANCE"},
            {"symbol": "SOLUSDT", "tv_symbol": "SOLUSDT", "exchange": "BINANCE"}
        ]
        with open(COINS_FILE, 'w') as f:
            json.dump(default_coins, f, indent=4)
        logger.info(f"Đã tạo file {COINS_FILE} với danh sách coin mặc định")
        return default_coins

# Lưu danh sách coin vào file JSON
def save_coins(coins):
    with open(COINS_FILE, 'w') as f:
        json.dump(coins, f, indent=4)
    logger.info(f"Đã lưu {len(coins)} coin vào {COINS_FILE}")

# Khởi tạo danh sách coin toàn cục
COIN_LIST = load_coins()

# Các biến toàn cục khác
SUPPORT_RESISTANCE = {}
PRICE_REPORTED = {coin["symbol"]: False for coin in COIN_LIST}
BREAKOUT_STATUS = {coin["symbol"]: {"support": False, "resistance": False, "message": ""} for coin in COIN_LIST}
analysis_results = []
last_updated = "Chưa cập nhật"
last_analysis_time = 0

# Khởi tạo TvDatafeed
try:
    tv = TvDatafeed(username="doluongdudz@gmail.com", password="DLDU01012000")
    logger.info("Đã kết nối thành công với TradingView")
except Exception as e:
    logger.error(f"Lỗi khi kết nối với TradingView: {e}")
    tv = None

# Các hàm khác (get_support_resistance, identify_candlestick_pattern, v.v.) giữ nguyên
# Chỉ liệt kê những phần cần sửa hoặc liên quan

def daily_analysis():
    global analysis_results, last_updated, last_analysis_time, COIN_LIST, PRICE_REPORTED, BREAKOUT_STATUS
    COIN_LIST = load_coins()  # Luôn tải lại từ file để đảm bảo đồng bộ
    for coin in COIN_LIST:
        if coin["symbol"] not in PRICE_REPORTED:
            PRICE_REPORTED[coin["symbol"]] = False
        if coin["symbol"] not in BREAKOUT_STATUS:
            BREAKOUT_STATUS[coin["symbol"]] = {"support": False, "resistance": False, "message": ""}
    
    analysis_results = []
    result = "PHÂN TÍCH HÀNG NGÀY (6:00 AM)\n\n"
    logger.info("Bắt đầu phân tích hàng ngày")
    for coin in COIN_LIST:
        # Logic phân tích giữ nguyên, chỉ đảm bảo COIN_LIST được dùng đúng
        coin_symbol = coin["symbol"]
        tv_symbol = coin["tv_symbol"]
        exchange = coin["exchange"]
        try:
            support_nearest, resistance_nearest, support_strong, resistance_strong, df = get_support_resistance(coin_symbol, tv_symbol, exchange, "4h", 45)
            if df is None:
                error_msg = f"Không thể phân tích {coin_symbol} do thiếu dữ liệu.\n\n"
                result += error_msg
                analysis_results.append(error_msg)
                continue
            # Phần còn lại của hàm daily_analysis() giữ nguyên
        except Exception as e:
            error_msg = f"Lỗi khi phân tích {coin_symbol}: {e}\n\n"
            logger.error(error_msg)
            result += error_msg
            analysis_results.append(error_msg)

    last_updated = time.strftime("%Y-%m-%d %H:%M:%S")
    last_analysis_time = time.time()

@app.route('/add_coin', methods=['POST'])
def add_coin():
    global COIN_LIST
    data = request.form
    symbol = data.get('symbol').upper()
    exchange = data.get('exchange').upper()

    if not symbol or not exchange:
        return jsonify({"status": "error", "message": "Vui lòng điền đầy đủ thông tin!"})

    if not symbol.endswith('USDT'):
        symbol += 'USDT'

    for coin in COIN_LIST:
        if coin["symbol"] == symbol:
            return jsonify({"status": "error", "message": f"{symbol} đã tồn tại trong danh sách!"})

    new_coin = {"symbol": symbol, "tv_symbol": symbol, "exchange": exchange}
    COIN_LIST.append(new_coin)
    save_coins(COIN_LIST)  # Lưu ngay vào file sau khi thêm
    logger.info(f"Đã thêm coin mới: {symbol}")

    daily_analysis()
    return jsonify({"status": "success", "message": f"Đã thêm {symbol} vào danh sách theo dõi!"})

@app.route('/remove_coin', methods=['POST'])
def remove_coin():
    global COIN_LIST
    symbol = request.form.get('symbol')

    if not symbol:
        return jsonify({"status": "error", "message": "Không tìm thấy coin để xóa!"})

    COIN_LIST = [coin for coin in COIN_LIST if coin["symbol"] != symbol]
    save_coins(COIN_LIST)  # Lưu ngay vào file sau khi xóa
    logger.info(f"Đã xóa coin: {symbol}")

    if symbol in SUPPORT_RESISTANCE:
        del SUPPORT_RESISTANCE[symbol]
    if symbol in PRICE_REPORTED:
        del PRICE_REPORTED[symbol]
    if symbol in BREAKOUT_STATUS:
        del BREAKOUT_STATUS[symbol]

    daily_analysis()
    return jsonify({"status": "success", "message": f"Đã xóa {symbol} khỏi danh sách theo dõi!"})

# Các route và hàm khác giữ nguyên

def startup():
    global COIN_LIST
    logger.info("Khởi động ứng dụng")
    COIN_LIST = load_coins()  # Tải lại danh sách coin khi khởi động
    initialize_support_resistance()
    daily_analysis()
    schedule_thread = Thread(target=run_schedule)
    schedule_thread.daemon = True
    schedule_thread.start()

if __name__ == "__main__":
    startup_thread = Thread(target=startup)
    startup_thread.start()
    app.run(debug=True, host='0.0.0.0', port=5000)