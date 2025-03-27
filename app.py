import requests
import pandas as pd
import schedule
import time
from threading import Thread
from flask import Flask, render_template

app = Flask(__name__)

# Danh sách coin quan tâm
COIN_LIST = ["BTC-USDT", "ETH-USDT", "XRP-USDT", "SOL-USDT"]

# Lưu trữ vùng hỗ trợ và kháng cự
SUPPORT_RESISTANCE = {}

# Lưu trữ trạng thái đã báo giá lần đầu
PRICE_REPORTED = {coin: False for coin in COIN_LIST}

# Lưu trữ trạng thái đã báo phá vỡ hỗ trợ/kháng cự
BREAKOUT_REPORTED = {coin: {"support": False, "resistance": False} for coin in COIN_LIST}

# Lưu trữ kết quả phân tích để hiển thị trên web
analysis_results = []
breakout_alerts = []

# Hàm lấy dữ liệu nến (ít nhất 100 cây nến 4H)
def get_support_resistance(coin, timeframe="4H", limit=100):
    url = f"https://www.okx.com/api/v5/market/history-candles?instId={coin}&bar={timeframe}&limit={limit}"
    response = requests.get(url)
    data = response.json()
    if 'data' not in data or not data['data']:
        return None, None, None, None, None
    df = pd.DataFrame(data['data'], columns=['timestamp', 'open', 'high', 'low', 'close', 'volume', 'volCcy', 'volCcyQuote', 'confirm'])
    df['timestamp'] = df['timestamp'].astype(int)
    df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
    df = df.astype({'open': float, 'high': float, 'low': float, 'close': float, 'volume': float})
    
    support_nearest = df['low'].rolling(window=20).min().iloc[-1]
    resistance_nearest = df['high'].rolling(window=20).max().iloc[-1]
    
    top_volume_candles = df.nlargest(2, 'volume')
    support_strong_candidate = top_volume_candles['low'].min()
    resistance_strong_candidate = top_volume_candles['high'].max()
    
    support_strong = min(support_strong_candidate, support_nearest)
    resistance_strong = max(resistance_strong_candidate, resistance_nearest)
    
    return support_nearest, resistance_nearest, support_strong, resistance_strong, df

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

# Hàm lấy giá hiện tại
def get_current_price(coin):
    url = f"https://www.okx.com/api/v5/market/ticker?instId={coin}"
    response = requests.get(url)
    data = response.json()
    if 'data' in data and data['data']:
        return float(data['data'][0]['last'])
    return None

# Hàm kiểm tra hợp lưu (chỉ trả về Có/Không)
def check_confluence(coin, trend_4h):
    timeframes = ["5m", "15m", "1H", "1D", "1W"]
    for timeframe in timeframes:
        _, _, _, _, df = get_support_resistance(coin, timeframe, limit=100)
        if df is not None:
            _, similarity, _, trend, _, _ = identify_price_pattern(df)
            if similarity >= 80 and trend is not None and trend == trend_4h:
                return True
    return False

# Hàm phân tích và đưa ra gợi ý hàng ngày
def daily_analysis():
    global analysis_results
    analysis_results = []  # Xóa kết quả cũ
    result = "PHÂN TÍCH HÀNG NGÀY (6:00 AM)\n\n"
    for coin in COIN_LIST:
        support_nearest, resistance_nearest, support_strong, resistance_strong, df = get_support_resistance(coin, "4H", limit=100)
        if df is not None:
            price_pattern, similarity, key_level, price_trend, pattern_low, pattern_high = identify_price_pattern(df)
            price = get_current_price(coin)
            entry_point = None
            recommended_entry = None
            
            coin_result = f"{coin}:\n"
            coin_result += f"Giá hiện tại: {price}\n"
            coin_result += f"Hỗ trợ gần nhất: {support_nearest:.2f}, Kháng cự gần nhất: {resistance_nearest:.2f}\n"
            coin_result += f"Hỗ trợ mạnh: {support_strong:.2f}, Kháng cự mạnh: {resistance_strong:.2f}\n"
            
            if price_pattern and similarity >= 80:
                coin_result += f"Mẫu hình giá: {price_pattern} (Tương đồng: {similarity:.2f}%)\n"
                
                trend_confirmed = identify_candlestick_pattern(df, price_trend)
                coin_result += f"Xác nhận xu hướng: {'Có' if trend_confirmed else 'Không'}\n"
                
                has_confluence = check_confluence(coin, price_trend)
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
            
            PRICE_REPORTED[coin] = True
        else:
            error_msg = f"Không thể phân tích {coin} do thiếu dữ liệu.\n\n"
            result += error_msg
            analysis_results.append(error_msg)

# Lên lịch và WebSocket
schedule.every().day.at("06:00").do(daily_analysis)

def run_schedule():
    while True:
        schedule.run_pending()
        time.sleep(60)

# Khởi tạo SUPPORT_RESISTANCE
for coin in COIN_LIST:
    support_nearest, resistance_nearest, _, _, _ = get_support_resistance(coin, "4H", limit=100)
    if support_nearest is not None and resistance_nearest is not None:
        SUPPORT_RESISTANCE[coin] = (support_nearest, resistance_nearest)

# Route chính để hiển thị kết quả trên web
@app.route('/')
def index():
    return render_template('index.html', analysis_results=analysis_results, breakout_alerts=breakout_alerts)

if __name__ == "__main__":
    # Chạy phân tích ban đầu
    daily_analysis()
    
    # Khởi động lịch trình
    schedule_thread = Thread(target=run_schedule)
    schedule_thread.start()