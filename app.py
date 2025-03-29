import pandas as pd
import schedule
import time
import logging
import json
import os
from threading import Thread
from flask import Flask, render_template, jsonify, request
from tvDatafeed import TvDatafeed, Interval

# Thiết lập logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Khởi tạo Flask app
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

# Hàm lấy dữ liệu từ TradingView (giữ nguyên từ mã cũ)
def get_support_resistance(coin_symbol, tv_symbol, exchange, timeframe="4h", limit=45):
    try:
        if tv is None:
            raise Exception("Không thể kết nối với TradingView")
        logger.info(f"Đang lấy dữ liệu nến cho {coin_symbol}")
        interval = Interval.in_4_hour if timeframe == "4h" else Interval.in_4_hour
        df = tv.get_hist(symbol=tv_symbol, exchange=exchange, interval=interval, n_bars=limit)
        if df is None or df.empty:
            logger.warning(f"Không có dữ liệu nến cho {coin_symbol}")
            return None, None, None, None, None
        df = df.reset_index()
        df = df.rename(columns={'datetime': 'timestamp', 'open': 'open', 'high': 'high', 'low': 'low', 'close': 'close', 'volume': 'volume'})
        df = df[['timestamp', 'open', 'high', 'low', 'close', 'volume']]
        support_nearest = df['low'].rolling(window=20).min().iloc[-1]
        resistance_nearest = df['high'].rolling(window=20).max().iloc[-1]
        top_volume_candles = df.nlargest(2, 'volume')
        support_strong = min(top_volume_candles['low'].min(), support_nearest)
        resistance_strong = max(top_volume_candles['high'].max(), resistance_nearest)
        logger.info(f"Lấy dữ liệu nến thành công cho {coin_symbol}")
        return support_nearest, resistance_nearest, support_strong, resistance_strong, df
    except Exception as e:
        logger.error(f"Lỗi khi lấy dữ liệu nến cho {coin_symbol}: {e}")
        return None, None, None, None, None

def get_current_price(tv_symbol, exchange):
    try:
        if tv is None:
            raise Exception("Không thể kết nối với TradingView")
        df = tv.get_hist(symbol=tv_symbol, exchange=exchange, interval=Interval.in_1_minute, n_bars=1)
        if df is None or df.empty:
            return None
        return float(df['close'].iloc[-1])
    except Exception as e:
        logger.error(f"Lỗi khi lấy giá hiện tại cho {tv_symbol}: {e}")
        return None

def get_price_24h_ago(tv_symbol, exchange):
    try:
        if tv is None:
            raise Exception("Không thể kết nối với TradingView")
        df = tv.get_hist(symbol=tv_symbol, exchange=exchange, interval=Interval.in_1_hour, n_bars=24)
        if df is None or df.empty:
            return None
        return float(df['close'].iloc[0])
    except Exception as e:
        logger.error(f"Lỗi khi lấy giá 24h trước cho {tv_symbol}: {e}")
        return None

def daily_analysis():
    global analysis_results, last_updated, last_analysis_time, COIN_LIST, PRICE_REPORTED, BREAKOUT_STATUS
    COIN_LIST = load_coins()  # Tải lại từ file để đồng bộ
    for coin in COIN_LIST:
        if coin["symbol"] not in PRICE_REPORTED:
            PRICE_REPORTED[coin["symbol"]] = False
        if coin["symbol"] not in BREAKOUT_STATUS:
            BREAKOUT_STATUS[coin["symbol"]] = {"support": False, "resistance": False, "message": ""}
    
    analysis_results = []
    logger.info("Bắt đầu phân tích hàng ngày")
    for coin in COIN_LIST:
        coin_symbol = coin["symbol"]
        tv_symbol = coin["tv_symbol"]
        exchange = coin["exchange"]
        try:
            support_nearest, resistance_nearest, support_strong, resistance_strong, df = get_support_resistance(coin_symbol, tv_symbol, exchange)
            if df is None:
                error_msg = f"Không thể phân tích {coin_symbol} do thiếu dữ liệu.\n\n"
                analysis_results.append(error_msg)
                continue
            price = get_current_price(tv_symbol, exchange)
            coin_result = f"{coin_symbol}:\nGiá hiện tại: {price if price else 'N/A'}\nHỗ trợ gần nhất: {support_nearest:.2f}, Kháng cự gần nhất: {resistance_nearest:.2f}\nHỗ trợ mạnh: {support_strong:.2f}, Kháng cự mạnh: {resistance_strong:.2f}\n\n"
            analysis_results.append(coin_result)
            SUPPORT_RESISTANCE[coin_symbol] = (support_nearest, resistance_nearest)
        except Exception as e:
            error_msg = f"Lỗi khi phân tích {coin_symbol}: {e}\n\n"
            logger.error(error_msg)
            analysis_results.append(error_msg)
    last_updated = time.strftime("%Y-%m-%d %H:%M:%S")
    last_analysis_time = time.time()

@app.route('/')
def index():
    return render_template('index.html', analysis_results=analysis_results, last_updated=last_updated, BREAKOUT_STATUS=BREAKOUT_STATUS)

@app.route('/get_prices', methods=['GET'])
def get_prices():
    prices = {}
    for coin in COIN_LIST:
        price = get_current_price(coin["tv_symbol"], coin["exchange"])
        if price is not None:
            prices[coin["symbol"]] = price
    return jsonify(prices)

@app.route('/get_price_change_24h', methods=['GET'])
def get_price_change_24h():
    price_changes = {}
    for coin in COIN_LIST:
        current_price = get_current_price(coin["tv_symbol"], coin["exchange"])
        price_24h_ago = get_price_24h_ago(coin["tv_symbol"], coin["exchange"])
        if current_price and price_24h_ago:
            change_percent = ((current_price - price_24h_ago) / price_24h_ago) * 100
            price_changes[coin["symbol"]] = change_percent
        else:
            price_changes[coin["symbol"]] = None
    return jsonify(price_changes)

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

    if any(coin["symbol"] == symbol for coin in COIN_LIST):
        return jsonify({"status": "error", "message": f"{symbol} đã tồn tại trong danh sách!"})

    new_coin = {"symbol": symbol, "tv_symbol": symbol, "exchange": exchange}
    COIN_LIST.append(new_coin)
    save_coins(COIN_LIST)
    daily_analysis()
    return jsonify({"status": "success", "message": f"Đã thêm {symbol} vào danh sách theo dõi!"})

@app.route('/remove_coin', methods=['POST'])
def remove_coin():
    global COIN_LIST
    symbol = request.form.get('symbol')
    if not symbol:
        return jsonify({"status": "error", "message": "Không tìm thấy coin để xóa!"})

    COIN_LIST = [coin for coin in COIN_LIST if coin["symbol"] != symbol]
    save_coins(COIN_LIST)
    if symbol in SUPPORT_RESISTANCE:
        del SUPPORT_RESISTANCE[symbol]
    if symbol in PRICE_REPORTED:
        del PRICE_REPORTED[symbol]
    if symbol in BREAKOUT_STATUS:
        del BREAKOUT_STATUS[symbol]
    daily_analysis()
    return jsonify({"status": "success", "message": f"Đã xóa {symbol} khỏi danh sách theo dõi!"})

def check_breakout():
    global last_analysis_time
    for coin in COIN_LIST:
        price = get_current_price(coin["tv_symbol"], coin["exchange"])
        if price and coin["symbol"] in SUPPORT_RESISTANCE:
            support, resistance = SUPPORT_RESISTANCE[coin["symbol"]]
            if price < support and not BREAKOUT_STATUS[coin["symbol"]]["support"]:
                BREAKOUT_STATUS[coin["symbol"]]["support"] = True
                BREAKOUT_STATUS[coin["symbol"]]["resistance"] = False
                BREAKOUT_STATUS[coin["symbol"]]["message"] = f"CẢNH BÁO: {coin['symbol']} đã phá vỡ hỗ trợ {support:.2f}! Giá hiện tại: {price}"
            elif price > resistance and not BREAKOUT_STATUS[coin["symbol"]]["resistance"]:
                BREAKOUT_STATUS[coin["symbol"]]["resistance"] = True
                BREAKOUT_STATUS[coin["symbol"]]["support"] = False
                BREAKOUT_STATUS[coin["symbol"]]["message"] = f"CẢNH BÁO: {coin['symbol']} đã phá vỡ kháng cự {resistance:.2f}! Giá hiện tại: {price}"
    if time.time() - last_analysis_time >= 4 * 3600:
        daily_analysis()

schedule.every(60).seconds.do(check_breakout)

def run_schedule():
    while True:
        schedule.run_pending()
        time.sleep(1)

def initialize_support_resistance():
    for coin in COIN_LIST:
        support_nearest, resistance_nearest, _, _, _ = get_support_resistance(coin["symbol"], coin["tv_symbol"], coin["exchange"])
        if support_nearest and resistance_nearest:
            SUPPORT_RESISTANCE[coin["symbol"]] = (support_nearest, resistance_nearest)

def startup():
    initialize_support_resistance()
    daily_analysis()
    Thread(target=run_schedule, daemon=True).start()

startup_thread = Thread(target=startup)
startup_thread.start()

if __name__ == "__main__":
    app.run(debug=True, host='0.0.0.0', port=5000)