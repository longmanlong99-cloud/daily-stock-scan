import os
import yfinance as yf
import pandas as pd
import numpy as np
from notion_client import Client
from datetime import datetime, timedelta

# --- 配置区域 ---
# 这里放你想监控的股票列表，以后可以随时加
WATCHLIST = ["RDW", "RCAT", "PLTR", "TSLA", "NVDA", "AMD", "AAPL"]

# --- 1. 连接 Notion ---
notion = Client(auth=os.environ.get("NOTION_TOKEN"))
database_id = os.environ.get("NOTION_DATABASE_ID")

def get_stock_data(ticker):
    """获取股票数据和期权链"""
    print(f"🔍 正在扫描: {ticker}...")
    stock = yf.Ticker(ticker)
    
    # 获取历史K线 (过去1年)
    hist = stock.history(period="1y")
    if hist.empty:
        return None
    
    current_price = hist['Close'].iloc[-1]
    volume = hist['Volume'].iloc[-1]
    avg_volume = hist['Volume'].tail(20).mean()
    
    # 计算技术指标
    ma200 = hist['Close'].tail(200).mean()
    is_breakout = volume > (avg_volume * 2) # 量能翻倍
    trend_status = "牛市" if current_price > ma200 else "熊市"
    
    # 计算 Max Pain (最大痛点)
    max_pain = calculate_max_pain(stock, current_price)
    
    return {
        "price": round(current_price, 2),
        "volume_ratio": round(volume / avg_volume, 1),
        "trend": trend_status,
        "max_pain": max_pain,
        "breakout": is_breakout
    }

def calculate_max_pain(stock, current_price):
    """计算期权最大痛点 (简化版)"""
    try:
        # 获取最近的一个期权到期日
        options_dates = stock.options
        if not options_dates:
            return None
        
        # 选最近的到期日
        chain = stock.option_chain(options_dates[0])
        calls = chain.calls
        puts = chain.puts
        
        # 简单的痛点估算：找到 Call 和 Put 持仓量(OI)最集中的行权价
        # (这里用简化算法，寻找 Open Interest 最大的价位作为参考)
        top_call_oi = calls.sort_values('openInterest', ascending=False).iloc[0]['strike']
        top_put_oi = puts.sort_values('openInterest', ascending=False).iloc[0]['strike']
        
        # 取两者平均或重心作为痛点参考
        return round((top_call_oi + top_put_oi) / 2, 2)
    except Exception as e:
        print(f"⚠️ {stock.ticker} 期权数据获取失败: {e}")
        return None

def push_to_notion(ticker, data):
    """将结果写入 Notion"""
    # 1. 确定标签 (L1/L2/L3)
    tags = []
    if data['breakout']:
        tags.append({"name": "L3-核心池"}) # 放量突破进核心
    elif data['trend'] == "牛市":
        tags.append({"name": "L2-观察池"})
    else:
        tags.append({"name": "L1-初选池"})
        
    # 2. 风险提示
    alert_text = ""
    if data['max_pain']:
        pain_diff = (data['price'] - data['max_pain']) / data['max_pain']
        if abs(pain_diff) > 0.15: # 偏离痛点 15%
            alert_text = f"⚠️ 偏离痛点 {pain_diff:.1%}"
            tags.append({"name": "风险警报"})

    # 3. 写入 Notion
    notion.pages.create(
        parent={"database_id": database_id},
        properties={
            "Name": {"title": [{"text": {"content": ticker}}]},
            "Tags": {"multi_select": tags},
            # 这里利用 Name 字段的副标题或者新建列来存价格，为了简单先写在标题里
            # 你以后可以在 Notion 增加 Price 列
        },
        children=[
            {
                "object": "block",
                "type": "paragraph",
                "paragraph": {
                    "rich_text": [
                        {"text": {"content": f"💰 现价: ${data['price']}\n"}},
                        {"text": {"content": f"📊 量比: {data['volume_ratio']}x (放量)\n" if data['breakout'] else f"📊 量比: {data['volume_ratio']}x\n"}},
                        {"text": {"content": f"🎯 Max Pain: ${data['max_pain']}\n" if data['max_pain'] else "🎯 Max Pain: 无数据\n"}},
                        {"text": {"content": f"{alert_text}", "annotations": {"color": "red"}}}
                    ]
                }
            }
        ]
    )
    print(f"✅ {ticker} 推送成功！")

# --- 主程序 ---
if __name__ == "__main__":
    print("🚀 开始执行每日选股任务...")
    for ticker in WATCHLIST:
        try:
            data = get_stock_data(ticker)
            if data:
                push_to_notion(ticker, data)
        except Exception as e:
            print(f"❌ 处理 {ticker} 时出错: {e}")
    
    print("🏁 所有任务完成！")
