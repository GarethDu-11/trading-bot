import requests
import pandas as pd
import schedule
import time
import logging
from threading import Thread
from flask import Flask, render_template
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# Thiết lập logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

app = Flask(__name__)

# Danh sách coin quan tâm (định dạng của CoinGecko)
COIN_LIST = [
    {"symbol": "BTCUSDT", "coingecko_id": "bitcoin"},
    {"symbol": "ETHUSDT", "coingecko_id": "ethereum"},
    {"symbol": "XRPUSDT", "coingecko_id": "ripple"},
    {"symbol": "SOLUSDT", "coingecko_id": "solana"}
]

# Lưu trữ vùng hỗ trợ và kháng cự
SUPPORT_RESISTANCE = {}

# Lưu trữ trạng thái đã báo giá lần đầu
PRICE_REPORTED = {coin["symbol"]: False for coin in COIN_LIST}

# Lưu trữ trạng thái đã báo phá vỡ hỗ trợ/kháng cự
BREAKOUT_REPORTED = {coin["symbol"]: {"support": False, "resistance": False} for coin in COIN_LIST}

# Lưu trữ kết quả phân tích để hiển thị trên web
analysis_results = []
breakout_alerts = []

# Lưu trữ thời gian cập nhật
last_updated = "Chưa cập nhật"

# Thiết lập session với cơ chế thử lại
session = requests.Session()
retries = Retry(total=3, backoff_factor=1, status_forcelist=[429, 500, 502, 503, 504])
session.mount('https://', HTTPAdapter(max_retries=retries))

# Hàm lấy dữ liệu nến từ CoinGecko
def get_support_resistance(coin_symbol, coingecko_id, timeframe="4h", limit=100):
    try:
        logger.info(f"Đang lấy dữ liệu nến cho {coin_symbol} ({coingecko_id})")
        # CoinGecko API: Lấy dữ liệu nến lịch sử
        # timeframe 4h -> dùng dữ liệu hàng ngày và tính toán lại
        url = f"https://api.coingecko.com/api/v3/coins/{coingecko_id}/ohlc"
        params = {
            "vs_currency": "usd",
            "days": "30"  # Lấy dữ liệu 30 ngày để có đủ nến
        }
        response = session.get(url, params=params, timeout=10)
        response.raise_for_status()
        data = response.json()
        if not data:
            logger.warning(f"Không có dữ liệu nến cho {coin_symbol}")
            return None, None, None, None, None

        # Chuyển dữ liệu thành DataFrame
        df = pd.DataFrame(data, columns=['timestamp', 'open', 'high', 'low', 'close'])
        df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
        df['volume'] = 0  # CoinGecko không cung cấp volume trong API OHLC, đặt mặc định là 0
        df = df.astype({'open': float, 'high': float, 'low': float, 'close': float, 'volume': float})

        # Lọc dữ liệu để lấy khoảng 100 nến gần nhất
        df = df.tail(limit)

        support_nearest = df['low'].rolling(window=20).min().iloc[-1]
        resistance_nearest = df['high'].rolling(window=20).max().iloc[-1]
        
        # Vì không có volume, dùng giá thấp/cao thay thế
        top_volume_candles = df.nlargest(2, 'high')  # Giả lập bằng cách lấy 2 nến có giá cao nhất
        support_strong_candidate = top_volume_candles['low'].min()
        resistance_strong_candidate = top_volume_candles['high'].max()
        
        support_strong = min(support_strong_candidate, support_nearest)
        resistance_strong = max(resistance_strong_candidate, resistance_nearest)
        
        logger.info(f"Lấy dữ liệu nến thành công cho {coin_symbol}")
        return support_nearest, resistance_nearest, support_strong, resistance_strong, df
    except Exception as e:
        logger.error(f"Lỗi khi lấy dữ liệu nến cho {coin_symbol}: {e}")
        return None, None, None, None, None

# Hàm nhận diện mẫu hình nến Nhật và xác nhận xu hướng
def identify_candlestick_pattern(df, price_trend):
    if len(df) < 3:
        return False
    
    latest = df.iloc[0]
    prev1 = df.iloc[1] if len(df) > 1 else None
    prev2 = df.iloc[2] if len(df) > 2 else None

    open_price = latest['open']
    close_price = latest['close']
    high_price = latest['high']
    low_price = latest['low']
    body_size = abs(open_price - close_price)
    upper_shadow = high_price - max(open_price, close_price)
    lower_shadow = min(open_price, close_price) - low_price

    candle_trend = None

    if body_size < (high_price - low_price) * 0.15 and abs(upper_shadow - lower_shadow) < body_size * 0.7:
        candle_trend = None
    if lower_shadow > body_size * 1.5 and upper_shadow < body_size * 0.5 and close_price > open_price:
        candle_trend = "bullish"
    if upper_shadow > body_size * 1.5 and lower_shadow < body_size * 0.5 and close_price < open_price:
        candle_trend = "bearish"
    if prev1 is not None:
        prev1_open = prev1['open']
        prev1_close = prev1['close']
        if close_price > open_price and prev1_close < prev1_open and open_price <= prev1_close and close_price >= prev1_open:
            candle_trend = "bullish"
        if close_price < open_price and prev1_close > prev1_open and open_price >= prev1_close and close_price <= prev1_open:
            candle_trend = "bearish"
    if prev2 is not None:
        prev1_open = prev1['open']
        prev1_close = prev1['close']
        prev1_high = prev1['high']
        prev1_low = prev1['low']
        prev2_open = prev2['open']
        prev2_close = prev2['close']
        if prev2_close < prev2_open and abs(prev1_close - prev1_open) < (prev1_high - prev1_low) * 0.5 and close_price > open_price and close_price > prev2_close:
            candle_trend = "bullish"
        if prev2_close > prev2_open and abs(prev1_close - prev1_open) < (prev1_high - prev1_low) * 0.5 and close_price < open_price and close_price < prev2_close:
            candle_trend = "bearish"
    if prev1 is not None:
        prev1_open = prev1['open']
        prev1_close = prev1['close']
        prev1_body_size = abs(prev1_open - prev1_close)
        if prev1_close < prev1_open and close_price > open_price and body_size < prev1_body_size * 0.5 and open_price > prev1_close and close_price < prev1_open:
            candle_trend = "bullish"
        if prev1_close > prev1_open and close_price < open_price and body_size < prev1_body_size * 0.5 and open_price < prev1_close and close_price > prev1_open:
            candle_trend = "bearish"
    if prev2 is not None and prev1 is not None:
        prev1_open = prev1['open']
        prev1_close = prev1['close']
        prev2_open = prev2['open']
        prev2_close = prev2['close']
        if (close_price > open_price and prev1_close > prev1_open and prev2_close > prev2_open and
            close_price > prev1_close and prev1_close > prev2_close):
            candle_trend = "bullish"
        if (close_price < open_price and prev1_close < prev1_open and prev2_close < prev2_open and
            close_price < prev1_close and prev1_close < prev2_close):
            candle_trend = "bearish"

    if candle_trend is None:
        return False
    return candle_trend == price_trend

# Hàm nhận diện mẫu hình giá phức tạp
def identify_price_pattern(df):
    if len(df) < 20:
        return None, 0, None, None, None, None
    
    highs = df['high'].values
    lows = df['low'].values
    closes = df['close'].values
    
    def check_head_and_shoulders(highs, lows, closes):
        if len(highs) < 20:
            return None, 0, None, None, None, None
        head_level = None
        for i in range(5, len(highs) - 5):
            left_shoulder = max(highs[i-5:i])
            head = highs[i]
            right_shoulder = max(highs[i+1:i+6])
            if head > left_shoulder and head > right_shoulder and abs(left_shoulder - right_shoulder) / head < 0.2:
                neckline = (min(lows[i-5:i]) + min(lows[i+1:i+6])) / 2
                similarity = 1.0 if abs(left_shoulder - right_shoulder) / head < 0.1 and closes[0] < neckline else 0.9
                head_level = head
                return "Head and Shoulders (Vai-Đầu-Vai)", similarity * 100, neckline, "bearish", head_level, neckline
        return None, 0, None, None, None, None
    
    def check_double_bottom(lows, highs, closes):
        if len(lows) < 20:
            return None, 0, None, None, None, None
        bottom_level = None
        for i in range(5, len(lows) - 5):
            left_bottom = min(lows[i-5:i])
            right_bottom = min(lows[i+1:i+6])
            if abs(left_bottom - right_bottom) / left_bottom < 0.2:
                resistance_line = max(highs[i-5:i+6])
                similarity = 1.0 if abs(left_bottom - right_bottom) / left_bottom < 0.1 and closes[0] > resistance_line else 0.9
                bottom_level = (left_bottom + right_bottom) / 2
                return "Double Bottom (Hai Đáy)", similarity * 100, resistance_line, "bullish", bottom_level, resistance_line
        return None, 0, None, None, None, None
    
    def check_double_top(highs, lows, closes):
        if len(highs) < 20:
            return None, 0, None, None, None, None
        top_level = None
        for i in range(5, len(highs) - 5):
            left_top = max(highs[i-5:i])
            right_top = max(highs[i+1:i+6])
            if abs(left_top - right_top) / left_top < 0.2:
                support_line = min(lows[i-5:i+6])
                similarity = 1.0 if abs(left_top - right_top) / left_top < 0.1 and closes[0] < support_line else 0.9
                top_level = (left_top + right_top) / 2
                return "Double Top (Hai Đỉnh)", similarity * 100, support_line, "bearish", top_level, support_line
        return None, 0, None, None, None, None
    
    def check_ascending_triangle(highs, lows, closes):
        if len(highs) < 20:
            return None, 0, None, None, None, None
        resistance = max(highs[-20:])
        support_points = []
        for i in range(5, len(lows) - 5):
            if lows[i] == min(lows[i-5:i+5]):
                support_points.append(lows[i])
        if len(support_points) >= 2 and all(support_points[i] < support_points[i+1] for i in range(len(support_points)-1)):
            support = min(support_points)
            similarity = 1.0 if closes[0] > resistance else 0.9
            return "Ascending Triangle (Tam Giác Tăng)", similarity * 100, resistance, "bullish", support, resistance
        return None, 0, None, None, None, None
    
    def check_descending_triangle(highs, lows, closes):
        if len(highs) < 20:
            return None, 0, None, None, None, None
        support = min(lows[-20:])
        resistance_points = []
        for i in range(5, len(highs) - 5):
            if highs[i] == max(highs[i-5:i+5]):
                resistance_points.append(highs[i])
        if len(resistance_points) >= 2 and all(resistance_points[i] > resistance_points[i+1] for i in range(len(resistance_points)-1)):
            resistance = max(resistance_points)
            similarity = 1.0 if closes[0] < support else 0.9
            return "Descending Triangle (Tam Giác Giảm)", similarity * 100, support, "bearish", support, resistance
        return None, 0, None, None, None, None

    patterns = [
        check_head_and_shoulders(highs, lows, closes),
        check_double_bottom(lows, highs, closes),
        check_double_top(highs, lows, closes),
        check_ascending_triangle(highs, lows, closes),
        check_descending_triangle(highs, lows, closes)
    ]
    best_pattern, best_similarity, key_level, trend, pattern_low, pattern_high = max(patterns, key=lambda x: x[1])
    if best_similarity > 0:
        return best_pattern, best_similarity, key_level, trend, pattern_low, pattern_high
    return None, 0, None, None, None, None

# Hàm lấy giá hiện tại từ CoinGecko
def get_current_price(coingecko_id):
    try:
        logger.info(f"Đang lấy giá hiện tại cho {coingecko_id}")
        url = f"https://api.coingecko.com/api/v3/simple/price"
        params = {
            "ids": coingecko_id,
            "vs_currencies": "usd"
        }
        response = session.get(url, params=params, timeout=10)
        response.raise_for_status()
        data = response.json()
        if coingecko_id in data and 'usd' in data[coingecko_id]:
            price = float(data[coingecko_id]['usd'])
            logger.info(f"Lấy giá hiện tại thành công cho {coingecko_id}: {price}")
            return price
        logger.warning(f"Không có dữ liệu giá cho {coingecko_id}")
        return None
    except Exception as e:
        logger.error(f"Lỗi khi lấy giá hiện tại cho {coingecko_id}: {e}")
        return None

# Hàm kiểm tra hợp lưu (chỉ trả về Có/Không)
def check_confluence(coin_symbol, coingecko_id, trend_4h):
    timeframes = ["5m", "15m", "1h", "1d", "1w"]
    for timeframe in timeframes:
        _, _, _, _, df = get_support_resistance(coin_symbol, coingecko_id, timeframe, limit=100)
        if df is not None:
            _, similarity, _, trend, _, _ = identify_price_pattern(df)
            if similarity >= 80 and trend is not None and trend == trend_4h:
                return True
    return False

# Hàm phân tích và đưa ra gợi ý hàng ngày
def daily_analysis():
    global analysis_results, last_updated
    analysis_results = []  # Xóa kết quả cũ
    result = "PHÂN TÍCH HÀNG NGÀY (6:00 AM)\n\n"
    logger.info("Bắt đầu phân tích hàng ngày")
    for coin in COIN_LIST:
        coin_symbol = coin["symbol"]
        coingecko_id = coin["coingecko_id"]
        try:
            support_nearest, resistance_nearest, support_strong, resistance_strong, df = get_support_resistance(coin_symbol, coingecko_id, "4h", limit=100)
            if df is not None:
                price_pattern, similarity, key_level, price_trend, pattern_low, pattern_high = identify_price_pattern(df)
                price = get_current_price(coingecko_id)
                entry_point = None
                recommended_entry = None
                
                coin_result = f"{coin_symbol}:\n"
                coin_result += f"Giá hiện tại: {price if price is not None else 'Không có dữ liệu'}\n"
                coin_result += f"Hỗ trợ gần nhất: {support_nearest:.2f}, Kháng cự gần nhất: {resistance_nearest:.2f}\n"
                coin_result += f"Hỗ trợ mạnh: {support_strong:.2f}, Kháng cự mạnh: {resistance_strong:.2f}\n"
                
                if price_pattern and similarity >= 80:
                    coin_result += f"Mẫu hình giá: {price_pattern} (Tương đồng: {similarity:.2f}%)\n"
                    
                    trend_confirmed = identify_candlestick_pattern(df, price_trend)
                    coin_result += f"Xác nhận xu hướng: {'Có' if trend_confirmed else 'Không'}\n"
                    
                    has_confluence = check_confluence(coin_symbol, coingecko_id, price_trend)
                    coin_result += f"Hợp lưu: {'Có' if has_confluence else 'Không'}\n"
                    
                    probability = 0
                    if similarity > 90:
                        probability += 50
                    elif similarity >= 80:
                        probability += 40
                    if trend_confirmed:
                        probability += 20
                    if has_confluence:
                        probability += 20
                    
                    trend_display = "Tăng" if price_trend == "bullish" else "Giảm"
                    coin_result += f"Xu hướng dự đoán: {trend_display} (Xác suất: {probability}%)\n"
                    
                    if price_trend == "bearish":
                        entry_point = key_level - (resistance_nearest - key_level) * 0.5
                    elif price_trend == "bullish":
                        entry_point = key_level + (key_level - support_nearest) * 0.5
                    
                    if similarity > 90:
                        if price_pattern == "Head and Shoulders (Vai-Đầu-Vai)":
                            distance = pattern_high - key_level
                            recommended_entry = key_level - distance * 0.1
                        elif price_pattern == "Double Bottom (Hai Đáy)":
                            distance = key_level - pattern_low
                            recommended_entry = key_level + distance * 0.1
                        elif price_pattern == "Double Top (Hai Đỉnh)":
                            distance = pattern_high - key_level
                            recommended_entry = key_level - distance * 0.1
                        elif price_pattern == "Ascending Triangle (Tam Giác Tăng)":
                            distance = pattern_high - pattern_low
                            recommended_entry = key_level + distance * 0.1
                        elif price_pattern == "Descending Triangle (Tam Giác Giảm)":
                            distance = pattern_high - pattern_low
                            recommended_entry = key_level - distance * 0.1
                else:
                    coin_result += "Không phát hiện mẫu hình giá rõ ràng.\n"
                
                if entry_point:
                    coin_result += f"Điểm vào lệnh: {'Bán' if price_trend == 'bearish' else 'Mua'} tại {entry_point:.2f}\n"
                if recommended_entry:
                    coin_result += f"Giá vào lệnh khuyến nghị: {'Bán' if price_trend == 'bearish' else 'Mua'} tại {recommended_entry:.2f}\n"
                
                coin_result += "\n"
                result += coin_result
                analysis_results.append(coin_result)
                
                PRICE_REPORTED[coin_symbol] = True
                logger.info(f"Phân tích thành công cho {coin_symbol}")
            else:
                error_msg = f"Không thể phân tích {coin_symbol} do thiếu dữ liệu.\n\n"
                result += error_msg
                analysis_results.append(error_msg)
                logger.warning(f"Không thể phân tích {coin_symbol} do thiếu dữ liệu")
        except Exception as e:
            error_msg = f"Lỗi khi phân tích {coin_symbol}: {e}\n\n"
            logger.error(error_msg)
            result += error_msg
            analysis_results.append(error_msg)
    last_updated = time.strftime("%Y-%m-%d %H:%M:%S")

# Hàm kiểm tra breakout định kỳ
def check_breakout():
    logger.info("Kiểm tra breakout")
    for coin in COIN_LIST:
        coin_symbol = coin["symbol"]
        coingecko_id = coin["coingecko_id"]
        try:
            price = get_current_price(coingecko_id)
            if price is None:
                continue
            if coin_symbol in SUPPORT_RESISTANCE:
                support, resistance = SUPPORT_RESISTANCE[coin_symbol]
                if price < support and not BREAKOUT_REPORTED[coin_symbol]["support"]:
                    alert = f"CẢNH BÁO: {coin_symbol} đã phá vỡ vùng hỗ trợ {support:.2f}! Giá hiện tại: {price}\n"
                    breakout_alerts.append(alert)
                    BREAKOUT_REPORTED[coin_symbol]["support"] = True
                    BREAKOUT_REPORTED[coin_symbol]["resistance"] = False
                    logger.info(f"Cảnh báo đột phá: {alert.strip()}")
                elif price > resistance and not BREAKOUT_REPORTED[coin_symbol]["resistance"]:
                    alert = f"CẢNH BÁO: {coin_symbol} đã phá vỡ vùng kháng cự {resistance:.2f}! Giá hiện tại: {price}\n"
                    breakout_alerts.append(alert)
                    BREAKOUT_REPORTED[coin_symbol]["resistance"] = True
                    BREAKOUT_REPORTED[coin_symbol]["support"] = False
                    logger.info(f"Cảnh báo đột phá: {alert.strip()}")
        except Exception as e:
            logger.error(f"Lỗi khi kiểm tra breakout cho {coin_symbol}: {e}")

# Lên lịch và kiểm tra breakout
schedule.every().day.at("06:00").do(daily_analysis)
schedule.every(60).seconds.do(check_breakout)

def run_schedule():
    logger.info("Khởi động lịch trình")
    while True:
        schedule.run_pending()
        time.sleep(1)

# Khởi tạo SUPPORT_RESISTANCE
def initialize_support_resistance():
    logger.info("Khởi tạo SUPPORT_RESISTANCE")
    for coin in COIN_LIST:
        coin_symbol = coin["symbol"]
        coingecko_id = coin["coingecko_id"]
        support_nearest, resistance_nearest, _, _, _ = get_support_resistance(coin_symbol, coingecko_id, "4h", limit=100)
        if support_nearest is not None and resistance_nearest is not None:
            SUPPORT_RESISTANCE[coin_symbol] = (support_nearest, resistance_nearest)
            logger.info(f"Đã khởi tạo SUPPORT_RESISTANCE cho {coin_symbol}: {support_nearest}, {resistance_nearest}")
        else:
            logger.warning(f"Không thể khởi tạo SUPPORT_RESISTANCE cho {coin_symbol}")

# Route chính để hiển thị kết quả trên web
@app.route('/')
def index():
    return render_template('index.html', analysis_results=analysis_results, breakout_alerts=breakout_alerts, last_updated=last_updated)

# Route để gọi phân tích thủ công
@app.route('/analyze', methods=['GET'])
def analyze():
    logger.info("Gọi phân tích thủ công qua /analyze")
    daily_analysis()
    return "Phân tích đã được thực hiện. Vui lòng làm mới trang để xem kết quả."

# Khởi động ứng dụng
def startup():
    logger.info("Khởi động ứng dụng")
    
    # Khởi tạo SUPPORT_RESISTANCE
    initialize_support_resistance()
    
    # Chạy phân tích ban đầu
    daily_analysis()
    
    # Khởi động lịch trình
    schedule_thread = Thread(target=run_schedule)
    schedule_thread.daemon = True
    schedule_thread.start()

# Chạy startup trong một thread riêng
startup_thread = Thread(target=startup)
startup_thread.start()

if __name__ == "__main__":
    # Chạy phân tích ban đầu (dùng cho môi trường cục bộ)
    daily_analysis()
    
    # Khởi động lịch trình
    schedule_thread = Thread(target=run_schedule)
    schedule_thread.start()