import os
import yfinance as yf
import pandas as pd
import numpy as np
from notion_client import Client
from datetime import datetime

# --- 配置区域 ---
WATCHLIST = ["RDW", "RCAT", "PLTR", "TSLA", "NVDA", "AMD", "AAPL"]
NOTION_TOKEN = os.environ.get("NOTION_TOKEN")
DATABASE_ID = os.environ.get("NOTION_DATABASE_ID")
notion = Client(auth=NOTION_TOKEN)

def get_enhanced_data(ticker):
    """获取股票数据、期权数据及高级指标"""
    print(f"🔍 深度扫描: {ticker}...")
    stock = yf.Ticker(ticker)
    hist = stock.history(period="1y")
    if hist.empty: return None

    # 基础指标
    current_price = hist['Close'].iloc[-1]
    prev_close = hist['Close'].iloc[-2]
    volume = hist['Volume'].iloc[-1]
    avg_volume = hist['Volume'].tail(20).mean()
    
    # 风险指标：ATR (用于动态止损)
    high_low = hist['High'] - hist['Low']
    high_cp = abs(hist['High'] - hist['Close'].shift())
    low_cp = abs(hist['Low'] - hist['Close'].shift())
    tr = pd.concat([high_low, high_cp, low_cp], axis=1).max(axis=1)
    atr = tr.rolling(14).mean().iloc[-1]
    stop_loss = current_price - (2.5 * atr) # 建议止损位

    # 漏斗逻辑逻辑指标
    ma200 = hist['Close'].tail(200).mean()
    vol_ratio = volume / avg_volume
    is_breakout = vol_ratio > 2.0 and current_price > prev_close # 放量上涨

    # 获取期权痛点 (Max Pain)
    max_pain = None
    try:
        if stock.options:
            chain = stock.option_chain(stock.options[0])
            calls, puts = chain.calls, chain.puts
            # 简化计算：OI最大的行权价均值
            top_call = calls.sort_values('openInterest', ascending=False).iloc[0]['strike']
            top_put = puts.sort_values('openInterest', ascending=False).iloc[0]['strike']
            max_pain = round((top_call + top_put) / 2, 2)
    except: pass

    return {
        "price": round(current_price, 2),
        "vol_ratio": round(vol_ratio, 1),
        "is_breakout": is_breakout,
        "max_pain": max_pain,
        "ma200": ma200,
        "stop_loss": round(stop_loss, 2),
        "history": hist['Close'].tail(10) # 用于后续相关性计算
    }

def process_funnel(all_data):
    """三级漏斗筛选逻辑 [cite: 7, 10]"""
    results = []
    # 1. 相关性去重：如果多只个股相关性 > 0.95，只留动量最强的 [cite: 30, 138]
    # (此处为简化演示，实际可调用 df.corr())

    for ticker, data in all_data.items():
        # 2. 自动打标与分级 [cite: 34]
        tags = []
        status = "L1-初选池" # 默认级别 [cite: 12]
        
        if data['price'] > data['ma200']:
            tags.append({"name": "趋势/白马"})
            status = "L2-观察池" # 站上200日线进入观察 [cite: 17]
            
        if data['is_breakout']:
            tags.append({"name": "放量突破"})
            status = "L3-核心池" # 放量突破进入核心 [cite: 27]
            
        if data['vol_ratio'] > 5.0:
            tags.append({"name": "高波动/博弈"})

        # 3. 风险预警文字 [cite: 39]
        alert = ""
        if data['max_pain']:
            diff = (data['price'] - data['max_pain']) / data['max_pain']
            if abs(diff) > 0.15:
                alert = f"⚠️ 偏离痛点 {diff:.1%}"
                tags.append({"name": "风险警报"})

        results.append({
            "ticker": ticker,
            "status": status,
            "tags": tags,
            "price": data['price'],
            "stop": data['stop_loss'],
            "alert": alert,
            "pain": data['max_pain']
        })
    return results

def sync_to_notion(results):
    """同步到 Notion 看板 [cite: 58, 63]"""
    for item in results:
        # 构建正文内容
        rich_text = [
            {"text": {"content": f"💰 现价: ${item['price']} | 🛡️ 建议止损: ${item['stop']}\n"}},
            {"text": {"content": f"🎯 Max Pain: ${item['pain'] if item['pain'] else '无'}\n"}}
        ]
        if item['alert']:
            rich_text.append({"text": {"content": item['alert']}, "annotations": {"color": "red"}})

        notion.pages.create(
            parent={"database_id": DATABASE_ID},
            properties={
                "Name": {"title": [{"text": {"content": item['ticker']}}]},
                "Status": {"select": {"name": item['status']}}, # 需在Notion创建名为Status的Select列
                "Tags": {"multi_select": item['tags']}
            },
            children=[{"object": "block", "type": "paragraph", "paragraph": {"rich_text": rich_text}}]
        )

if __name__ == "__main__":
    raw_data = {}
    for t in WATCHLIST:
        d = get_enhanced_data(t)
        if d: raw_data[t] = d
    
    processed_results = process_funnel(raw_data)
    sync_to_notion(processed_results)
    print("🏁 系统漏斗扫描并同步完成！")

