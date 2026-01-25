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
        WATCHLIST = list(set([l.strip().upper() for l in f if l.strip()]))

DATA_CACHE = {}

def calculate_max_pain(ticker, stock_obj):
    """计算期权最大痛点 (PDF 第3页逻辑)"""
    try:
        options_dates = stock_obj.options
        if not options_dates: return None, None
        
        # 只取最近的一个到期日 (防封 + 聚焦短期)
        expiry = options_dates[0]
        chain = stock_obj.option_chain(expiry)
        
        calls = chain.calls[['strike', 'openInterest']].dropna()
        puts = chain.puts[['strike', 'openInterest']].dropna()
        
        if calls.empty or puts.empty: return None, None

        all_strikes = sorted(list(set(calls['strike'].tolist() + puts['strike'].tolist())))
        min_loss = float('inf')
        max_pain_price = 0
        
        for s in all_strikes:
            # 庄家赔付计算
            call_loss = np.maximum(0, s - calls['strike']) * calls['openInterest']
            put_loss = np.maximum(0, puts['strike'] - s) * puts['openInterest']
            total_loss = call_loss.sum() + put_loss.sum()
            
            if total_loss < min_loss:
                min_loss = total_loss
                max_pain_price = s
                
        return max_pain_price, expiry
    except:
        return None, None

print(f"🚀 [Fetch] 开始抓取 {len(WATCHLIST)} 只股票...")

for ticker in WATCHLIST:
    try:
        print(f"📥 {ticker}...", end="")
        stock = yf.Ticker(ticker)
        
        # 1. 获取 1 年数据 (用于 MA200)
        hist = stock.history(period="1y")
        if hist.empty:
            print(" ❌ 无数据")
            continue
            
        current_price = round(hist['Close'].iloc[-1], 2)
        volume = int(hist['Volume'].iloc[-1])
        
        # 2. 计算技术指标
        ma200 = hist['Close'].rolling(window=200).mean().iloc[-1]
        if pd.isna(ma200): ma200 = current_price # 新股容错
        
        avg_vol_5d = hist['Volume'].tail(5).mean()
        vol_ratio = round(volume / avg_vol_5d, 1) if avg_vol_5d > 0 else 0
        
        # 3. 计算 Max Pain
        max_pain, expiry = calculate_max_pain(ticker, stock)
        
        DATA_CACHE[ticker] = {
            "price": current_price,
            "volume": volume,
            "ma200": round(ma200, 2),
            "vol_ratio": vol_ratio,
            "max_pain": max_pain,
            "last_updated": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        }
        print(f" ✅ ${current_price} | Pain:${max_pain}")
        
        # 关键：强制休息 2 秒 (防封)
        time.sleep(2)
        
    except Exception as e:
        print(f" ❌ Error: {e}")

# 保存缓存
with open("daily_cache.json", "w") as f:
    json.dump(DATA_CACHE, f, indent=4)
print("💾 数据已保存至 daily_cache.json")
