import yfinance as yf
import pandas as pd
import numpy as np
import json
import time
import os
from datetime import datetime

# ==========================================
# 1. 配置区
# ==========================================
WATCHLIST = ["RDW", "RCAT", "PLTR", "TSLA", "NVDA", "AMD", "AAPL"]
if os.path.exists("stocks.txt"):
    with open("stocks.txt", "r") as f:
        # 读取股票列表，去重并转大写
        WATCHLIST = list(set([l.strip().upper() for l in f if l.strip()]))

DATA_CACHE = {}

# ==========================================
# 2. 技术指标计算函数
# ==========================================
def calculate_rsi(series, period=14):
    """计算 RSI 相对强弱指标"""
    delta = series.diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=period).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()
    rs = gain / loss
    return 100 - (100 / (1 + rs))

def calculate_atr(high, low, close, period=14):
    """计算 ATR 平均真实波幅 (用于动态止损)"""
    high_low = high - low
    high_close = np.abs(high - close.shift())
    low_close = np.abs(low - close.shift())
    ranges = pd.concat([high_low, high_close, low_close], axis=1)
    true_range = np.max(ranges, axis=1)
    return true_range.rolling(window=period).mean()

def calculate_options_data(ticker, stock_obj):
    """
    🔥【专业版算法修正】
    1. PCR (Put/Call Ratio): 计算未来 4 个到期日的【总持仓量比值】。
       (对齐专业网站数据，样本更大更准确)
    2. Max Pain (最大痛点): 依然锁定【单一主力合约】。
       (因为痛点必须针对特定日期才有引力意义，不能混在一起算)
    """
    try:
        options_dates = stock_obj.options
        if not options_dates: return None, None, None
        
        # 扫描范围：未来 4 个到期日 (涵盖近月主力)
        check_limit = min(4, len(options_dates))
        
        # --- 变量初始化 ---
        global_call_oi = 0  # 累计所有日期的 Call
        global_put_oi = 0   # 累计所有日期的 Put
        
        best_expiry = None
        max_single_oi = 0      # 单期最大持仓量
        best_chain_data = None # 用来存主力合约的数据(算痛点用)
        
        # --- 1. 循环扫描 (累加 PCR + 寻找主力战场) ---
        for i in range(check_limit):
            expiry = options_dates[i]
            try:
                # 获取该日期的期权链
                chain = stock_obj.option_chain(expiry)
                calls = chain.calls
                puts = chain.puts
                
                # A. 累加到全局池 (用于算 Total PCR)
                # fillna(0) 防止空数据报错
                c_oi = calls['openInterest'].fillna(0).sum() if not calls.empty else 0
                p_oi = puts['openInterest'].fillna(0).sum() if not puts.empty else 0
                
                global_call_oi += c_oi
                global_put_oi += p_oi
                
                # B. 寻找主力合约 (用于算 Max Pain)
                # 谁的持仓量最大，谁就是庄家主战场
                current_total = c_oi + p_oi
                if current_total > max_single_oi:
                    max_single_oi = current_total
                    best_expiry = expiry
                    best_chain_data = (calls, puts)
                    
            except Exception:
                continue
        
        # --- 2. 计算 Total PCR (涵盖 4 期) ---
        # 现在的 PCR 是基于未来一个月的总量计算的，非常稳定且具备参考性
        pcr = round(global_put_oi / global_call_oi, 2) if global_call_oi > 0 else 0
        
        # --- 3. 噪音过滤 & 痛点计算 ---
        # 如果连主力合约都没量 (<2000张)，说明这票没人玩期权，痛点无效
        if max_single_oi < 2000 or best_chain_data is None:
            # 返回 None 的痛点，但返回计算好的 PCR (PCR 还是有参考价值的)
            return None, None, pcr 

        # 提取主力合约数据
        calls_best, puts_best = best_chain_data
        calls_best = calls_best[['strike', 'openInterest']].dropna()
        puts_best = puts_best[['strike', 'openInterest']].dropna()

        # 计算 Max Pain (仅基于主力合约)
        all_strikes = sorted(list(set(calls_best['strike'].tolist() + puts_best['strike'].tolist())))
        min_loss = float('inf')
        max_pain_price = 0
        
        for s in all_strikes:
            # 假设收盘价是 s，卖方亏多少
            call_loss = np.maximum(0, s - calls_best['strike']) * calls_best['openInterest']
            put_loss = np.maximum(0, puts_best['strike'] - s) * puts_best['openInterest']
            
            total_loss = call_loss.sum() + put_loss.sum()
            
            if total_loss < min_loss:
                min_loss = total_loss
                max_pain_price = s
                
        return max_pain_price, best_expiry, pcr

    except Exception as e:
        # print(f"  ❌ 期权计算出错 {ticker}: {e}")
        return None, None, None

# ==========================================
# 3. 主程序循环
# ==========================================
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
        
        # 2. 计算均线 (MA20, MA60, MA200)
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
        
        # 🔥 调用升级后的期权计算函数 (4期PCR + 单期痛点)
        max_pain, expiry, pcr = calculate_options_data(ticker, stock)
        
        # 4. 存入缓存
        DATA_CACHE[ticker] = {
            "price": current_price,
            "volume": volume,
            "ma20": round(ma20, 2),
            "ma60": round(ma60, 2),
            "ma200": round(ma200, 2),
            "vol_ratio": vol_ratio,
            "rsi": rsi,
            "atr": atr,
            "max_pain": max_pain,
            "pcr": pcr,
            "last_updated": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        }
        
        # 打印简报
        pain_info = f"Pain:${max_pain}" if max_pain else "Pain:--"
        print(f" ✅ ${current_price} | RSI:{rsi} | PCR:{pcr} | {pain_info}")
        
        time.sleep(1.5) # 保持防封节奏
        
    except Exception as e:
        print(f" ❌ Error: {e}")

# ==========================================
# 4. 保存结果
# ==========================================
with open("daily_cache.json", "w") as f:
    json.dump(DATA_CACHE, f, indent=4)
print("💾 数据已更新，包含升级版 PCR 和痛点数据。")
