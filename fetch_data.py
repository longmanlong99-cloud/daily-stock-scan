import yfinance as yf
import pandas as pd
import numpy as np
import json
import time
import os
from datetime import datetime

# --- 配置区 ---
WATCHLIST = ["RDW", "RCAT", "PLTR", "TSLA", "NVDA", "AMD", "AAPL"]
if os.path.exists("stocks.txt"):
    with open("stocks.txt", "r") as f:
        # 读取股票列表，去重并转大写
        WATCHLIST = list(set([l.strip().upper() for l in f if l.strip()]))

DATA_CACHE = {}

# --- 技术指标计算函数 ---
def calculate_rsi(series, period=14):
    """计算 RSI"""
    delta = series.diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=period).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()
    rs = gain / loss
    return 100 - (100 / (1 + rs))

def calculate_atr(high, low, close, period=14):
    """计算 ATR"""
    high_low = high - low
    high_close = np.abs(high - close.shift())
    low_close = np.abs(low - close.shift())
    ranges = pd.concat([high_low, high_close, low_close], axis=1)
    true_range = np.max(ranges, axis=1)
    return true_range.rolling(window=period).mean()

def calculate_options_data(ticker, stock_obj):
    """计算 PCR 和 Max Pain"""
    try:
        options_dates = stock_obj.options
        if not options_dates: return None, None, None
        
        expiry = options_dates[0]
        chain = stock_obj.option_chain(expiry)
        
        calls = chain.calls[['strike', 'openInterest']].dropna()
        puts = chain.puts[['strike', 'openInterest']].dropna()
        
        if calls.empty or puts.empty: return None, None, None

        total_call_oi = calls['openInterest'].sum()
        total_put_oi = puts['openInterest'].sum()
        pcr = round(total_put_oi / total_call_oi, 2) if total_call_oi > 0 else 0

        all_strikes = sorted(list(set(calls['strike'].tolist() + puts['strike'].tolist())))
        min_loss = float('inf')
        max_pain_price = 0
        
        for s in all_strikes:
            call_loss = np.maximum(0, s - calls['strike']) * calls['openInterest']
            put_loss = np.maximum(0, puts['strike'] - s) * puts['openInterest']
            total_loss = call_loss.sum() + put_loss.sum()
            
            if total_loss < min_loss:
                min_loss = total_loss
                max_pain_price = s
                
        return max_pain_price, expiry, pcr
    except:
        return None, None, None

print(f"🚀 [Fetch] 开始深度抓取 {len(WATCHLIST)} 只股票 (含 MA20/MA60/MA200)...")

for ticker in WATCHLIST:
    try:
        print(f"📥 {ticker}...", end="")
        stock = yf.Ticker(ticker)
        
        # 1. 获取数据 (1年数据足够计算 MA200)
        hist = stock.history(period="1y")
        if hist.empty:
            print(" ❌ 无数据")
            continue
            
        current_price = round(hist['Close'].iloc[-1], 2)
        volume = int(hist['Volume'].iloc[-1])
        
        # 2. 计算均线 (MA20, MA60, MA200) - 核心修改点
        ma20 = hist['Close'].rolling(window=20).mean().iloc[-1]
        ma60 = hist['Close'].rolling(window=60).mean().iloc[-1]
        ma200 = hist['Close'].rolling(window=200).mean().iloc[-1]
        
        # 补救措施：如果是新股，数据不足导致 MA 为空，则用现价代替
        if pd.isna(ma20): ma20 = current_price
        if pd.isna(ma60): ma60 = current_price
        if pd.isna(ma200): ma200 = current_price

        # 3. 计算其他指标
        avg_vol_5d = hist['Volume'].tail(5).mean()
        vol_ratio = round(volume / avg_vol_5d, 1) if avg_vol_5d > 0 else 0
        
        rsi_series = calculate_rsi(hist['Close'])
        rsi = round(rsi_series.iloc[-1], 1) if not pd.isna(rsi_series.iloc[-1]) else 50
        
        atr_series = calculate_atr(hist['High'], hist['Low'], hist['Close'])
        atr = round(atr_series.iloc[-1], 2) if not pd.isna(atr_series.iloc[-1]) else 0
        
        max_pain, expiry, pcr = calculate_options_data(ticker, stock)
        
        # 4. 存入缓存
        DATA_CACHE[ticker] = {
            "price": current_price,
            "volume": volume,
            "ma20": round(ma20, 2),  # 新增
            "ma60": round(ma60, 2),  # 新增
            "ma200": round(ma200, 2),
            "vol_ratio": vol_ratio,
            "rsi": rsi,
            "atr": atr,
            "max_pain": max_pain,
            "pcr": pcr,
            "last_updated": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        }
        print(f" ✅ ${current_price} | MA60:${round(ma60, 1)} | RSI:{rsi}")
        
        time.sleep(1.5) # 稍微快一点点
        
    except Exception as e:
        print(f" ❌ Error: {e}")

with open("daily_cache.json", "w") as f:
    json.dump(DATA_CACHE, f, indent=4)
print("💾 数据已更新，包含 MA20/MA60 均线数据。")
