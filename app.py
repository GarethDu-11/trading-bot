import yfinance as yf
import pandas as pd
import schedule
import time
import logging
from threading import Thread
from flask import Flask, render_template

# Thiết lập logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

app = Flask(__name__)

# Danh sách coin quan tâm (định dạng của Yahoo Finance)
COIN_LIST = [
    {"symbol": "BTCUSDT", "yahoo_symbol": "BTC-USD"},
    {"symbol": "ETHUSDT", "yahoo_symbol": "ETH-USD"},
    {"symbol": "XRPUSDT", "yahoo_symbol": "XRP-USD"},
    {"symbol": "SOLUSDT", "yahoo_symbol": "SOL-USD"}
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

# Hàm lấy dữ liệu nến từ Yahoo Finance
def get_support_resistance(coin_symbol, yahoo_symbol, timeframe="4h", limit=100):
    try:
        logger.info(f"Đang lấy dữ liệu nến cho {coin_symbol} ({yahoo_symbol})")
        ticker = yf.Ticker(yahoo_symbol)
        df = ticker.history(period="1mo", interval="4h")  # Lấy dữ liệu 1 tháng, khung 4h
        if df.empty:
            logger.warning(f"Không có dữ liệu nến cho {coin_symbol}")
            return None, None, None, None, None

        df = df.reset_index()
        df = df.rename(columns={
            'Date': 'timestamp',
            'Open': 'open',
            'High': 'high',
            'Low': 'low',
            'Close': 'close',
            'Volume': 'volume'
        })
        df = df[['timestamp', 'open', 'high', 'low', 'close', 'volume']]
        df = df.tail(limit)

        support_nearest = df['low'].rolling(window=20).min().iloc[-1]
        resistance_nearest = df['high'].rolling(window=20).max().iloc[-1]
        
        top_volume_candles = df.nlargest(2, 'volume')
        support_strong_candidate = top_volume_candles['low'].min()
        resistance_strong_candidate = top_volume_candles['high'].max()
        
        support_strong = min(support_strong_candidate, support_nearest)
        resistance_strong = max(resistance_strong_candidate, resistance_nearest)
        
        logger.info(f"Lấy dữ liệu nến thành công cho {coin_symbol}")
        return support_nearest, resistance_nearest, support_strong, resistance_strong, df
    except Exception as e:
        logger.error(f"Lỗi khi lấy dữ liệu nến cho {coin_symbol}: {e}")
        return None, None, None, None, None

# Hàm lấy giá hiện tại từ Yahoo Finance
def get_current_price(yahoo_symbol):
    try:
        logger.info(f"Đang lấy giá hiện tại cho {yahoo_symbol}")
        ticker = yf.Ticker(yahoo_symbol)
        price = ticker.info['regularMarketPrice']
        logger.info(f"Lấy giá hiện tại thành công cho {yahoo_symbol}: {price}")
        return float(price)
    except Exception as e:
        logger.error(f"Lỗi khi lấy giá hiện tại cho {yahoo_symbol}: {e}")
        return None

# Hàm kiểm tra hợp lưu (chỉ trả về Có/Không)
def check_confluence(coin_symbol, yahoo_symbol, trend_4h):
    timeframes = ["5m", "15m", "1h", "1d", "1w"]
    for timeframe in timeframes:
        _, _, _, _, df = get_support_resistance(coin_symbol, yahoo_symbol, timeframe, limit=100)
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
        yahoo_symbol = coin["yahoo_symbol"]
        try:
            support_nearest, resistance_nearest, support_strong, resistance_strong, df = get_support_resistance(coin_symbol, yahoo_symbol, "4h", limit=100)
            if df is not None:
                price_pattern, similarity, key_level, price_trend, pattern_low, pattern_high = identify_price_pattern(df)
                price = get_current_price(yahoo_symbol)
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
                    
                    has_confluence = check_confluence(coin_symbol, yahoo_symbol, price_trend)
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
        yahoo_symbol = coin["yahoo_symbol"]
        try:
            price = get_current_price(yahoo_symbol)
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
        yahoo_symbol = coin["yahoo_symbol"]
        support_nearest, resistance_nearest, _, _, _ = get_support_resistance(coin_symbol, yahoo_symbol, "4h", limit=100)
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