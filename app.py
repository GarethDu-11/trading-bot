import pandas as pd
import schedule
import time
import logging
import json
import os
from threading import Thread
from flask import Flask, render_template, jsonify, request
from tvDatafeed import TvDatafeed, Interval
from scipy.stats import linregress

# Thiết lập logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

app = Flask(__name__, template_folder='templates')
COINS_FILE = "coins.json"

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

def save_coins(coins):
    with open(COINS_FILE, 'w') as f:
        json.dump(coins, f, indent=4)
    logger.info(f"Đã lưu {len(coins)} coin vào {COINS_FILE}")

COIN_LIST = load_coins()
SUPPORT_RESISTANCE = {}
PRICE_REPORTED = {coin["symbol"]: False for coin in COIN_LIST}
BREAKOUT_STATUS = {coin["symbol"]: {"support": False, "resistance": False, "message": ""} for coin in COIN_LIST}
analysis_results = []
last_updated = "Chưa cập nhật"
last_analysis_time = 0

try:
    tv = TvDatafeed(username="doluongdudz@gmail.com", password="DLDU01012000")
    logger.info("Đã kết nối thành công với TradingView")
except Exception as e:
    logger.error(f"Lỗi khi kết nối với TradingView: {e}")
    tv = None

def get_support_resistance(coin_symbol, tv_symbol, exchange, timeframe="4h", limit=45):
    try:
        if tv is None:
            raise Exception("Không thể kết nối với TradingView")
        logger.info(f"Đang lấy dữ liệu nến cho {coin_symbol} ({timeframe})")
        interval = Interval.in_4_hour if timeframe == "4h" else Interval.in_1_day
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

def identify_candlestick_pattern(df_4h):
    """Xác định mẫu hình nến trên khung 4H"""
    if df_4h is None or len(df_4h) < 2:
        return "Không đủ dữ liệu để xác định mẫu hình nến"
    last_candle = df_4h.iloc[-1]
    prev_candle = df_4h.iloc[-2]
    body = abs(last_candle['close'] - last_candle['open'])
    upper_shadow = last_candle['high'] - max(last_candle['close'], last_candle['open'])
    lower_shadow = min(last_candle['close'], last_candle['open']) - last_candle['low']

    if body < upper_shadow and body < lower_shadow and upper_shadow > 2 * body and lower_shadow > 2 * body:
        return "Doji (Có thể đảo chiều)"
    elif last_candle['close'] > last_candle['open'] and prev_candle['close'] < prev_candle['open']:
        return "Bullish Engulfing (Tín hiệu tăng)"
    elif last_candle['close'] < last_candle['open'] and prev_candle['close'] > prev_candle['open']:
        return "Bearish Engulfing (Tín hiệu giảm)"
    return "Không phát hiện mẫu hình rõ ràng"

def analyze_price_pattern(df_1d):
    """Phân tích mẫu hình giá trên khung 1D (90 ngày), tính độ tương đồng"""
    if df_1d is None or len(df_1d) < 10:
        return "Không đủ dữ liệu", 0
    
    # Ví dụ đơn giản: Tìm mẫu hình Double Top/Double Bottom
    highs = df_1d['high'].rolling(window=5).max()
    lows = df_1d['low'].rolling(window=5).min()
    recent_highs = highs[-10:].dropna()
    recent_lows = lows[-10:].dropna()

    if len(recent_highs) >= 2 and len(recent_lows) >= 2:
        last_high = recent_highs.iloc[-1]
        prev_high = recent_highs.iloc[-2]
        last_low = recent_lows.iloc[-1]
        prev_low = recent_lows.iloc[-2]

        # Double Top
        if abs(last_high - prev_high) / prev_high < 0.02:  # Sai lệch < 2%
            similarity = 100 - (abs(last_high - prev_high) / prev_high * 100)
            return "Double Top (Tín hiệu giảm)", similarity
        # Double Bottom
        elif abs(last_low - prev_low) / prev_low < 0.02:
            similarity = 100 - (abs(last_low - prev_low) / prev_low * 100)
            return "Double Bottom (Tín hiệu tăng)", similarity
    
    return "Không phát hiện mẫu hình giá", 0

def check_confluence(price, support_nearest, resistance_nearest, pattern_4h, pattern_1d, similarity):
    """Kiểm tra hợp lưu"""
    confluence = []
    if abs(price - support_nearest) / support_nearest < 0.01:  # Giá gần hỗ trợ
        confluence.append("Hỗ trợ gần nhất")
    if abs(price - resistance_nearest) / resistance_nearest < 0.01:  # Giá gần kháng cự
        confluence.append("Kháng cự gần nhất")
    if "Engulfing" in pattern_4h or "Doji" in pattern_4h:
        confluence.append(f"Mẫu nến 4H: {pattern_4h}")
    if similarity > 80:
        confluence.append(f"Mẫu hình giá 1D: {pattern_1d} ({similarity:.2f}%)")
    return ", ".join(confluence) if confluence else "Không có hợp lưu đáng kể"

def determine_trend(df_1d):
    """Xác định xu hướng chung bằng cách nối đỉnh và đáy (nến 1D)"""
    if df_1d is None or len(df_1d) < 20:
        return "Không đủ dữ liệu để xác định xu hướng"
    
    highs = df_1d['high'].rolling(window=5).max().dropna()
    lows = df_1d['low'].rolling(window=5).min().dropna()
    
    high_slope, _, _, _, _ = linregress(range(len(highs)), highs)
    low_slope, _, _, _, _ = linregress(range(len(lows)), lows)

    if high_slope > 0 and low_slope > 0:
        return "Xu hướng tăng"
    elif high_slope < 0 and low_slope < 0:
        return "Xu hướng giảm"
    else:
        return "Xu hướng sideways"

def daily_analysis():
    global analysis_results, last_updated, last_analysis_time, COIN_LIST, PRICE_REPORTED, BREAKOUT_STATUS
    COIN_LIST = load_coins()
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
            # Dữ liệu 4H cho hỗ trợ/kháng cự và mẫu nến
            support_nearest, resistance_nearest, support_strong, resistance_strong, df_4h = get_support_resistance(coin_symbol, tv_symbol, exchange, "4h", 45)
            # Dữ liệu 1D cho mẫu hình giá và xu hướng
            _, _, _, _, df_1d = get_support_resistance(coin_symbol, tv_symbol, exchange, "1d", 90)
            
            if df_4h is None or df_1d is None:
                error_msg = f"Không thể phân tích {coin_symbol} do thiếu dữ liệu.\n\n"
                analysis_results.append(error_msg)
                continue
            
            price = get_current_price(tv_symbol, exchange)
            pattern_4h = identify_candlestick_pattern(df_4h)
            pattern_1d, similarity = analyze_price_pattern(df_1d)
            confluence = check_confluence(price, support_nearest, resistance_nearest, pattern_4h, pattern_1d, similarity)
            trend = determine_trend(df_1d)

            coin_result = (
                f"{coin_symbol}:\n"
                f"Giá hiện tại: {price if price else 'N/A'}\n"
                f"Hỗ trợ gần nhất: {support_nearest:.2f}, Kháng cự gần nhất: {resistance_nearest:.2f}\n"
                f"Hỗ trợ mạnh: {support_strong:.2f}, Kháng cự mạnh: {resistance_strong:.2f}\n"
                f"Mẫu nến 4H: {pattern_4h}\n"
                f"Mẫu hình giá 1D: {pattern_1d} ({similarity:.2f}% tương đồng)\n"
                f"Hợp lưu: {confluence}\n"
                f"Xu hướng chung: {trend}\n\n"
            )
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

@app.route('/analyze', methods=['GET'])
def analyze():
    logger.info("Gọi phân tích thủ công qua /analyze")
    daily_analysis()
    return "Phân tích đã được thực hiện. Vui lòng làm mới trang để xem kết quả."

@app.route('/reset_coins', methods=['POST'])
def reset_coins():
    global COIN_LIST, SUPPORT_RESISTANCE, PRICE_REPORTED, BREAKOUT_STATUS
    default_coins = [
        {"symbol": "BTCUSDT", "tv_symbol": "BTCUSDT", "exchange": "BINANCE"},
        {"symbol": "ETHUSDT", "tv_symbol": "ETHUSDT", "exchange": "BINANCE"},
        {"symbol": "XRPUSDT", "tv_symbol": "XRPUSDT", "exchange": "BINANCE"},
        {"symbol": "SOLUSDT", "tv_symbol": "SOLUSDT", "exchange": "BINANCE"}
    ]
    COIN_LIST = default_coins
    save_coins(COIN_LIST)
    SUPPORT_RESISTANCE = {}
    PRICE_REPORTED = {coin["symbol"]: False for coin in COIN_LIST}
    BREAKOUT_STATUS = {coin["symbol"]: {"support": False, "resistance": False, "message": ""} for coin in COIN_LIST}
    daily_analysis()
    logger.info("Đã reset danh sách coin về mặc định")
    return jsonify({"status": "success", "message": "Đã reset danh sách coin về mặc định!"})

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