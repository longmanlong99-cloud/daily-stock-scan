import os
import yfinance as yf
import pandas as pd
import numpy as np
from notion_client import Client
from datetime import datetime

# --- 配置区域 ---
# 你想监控的股票列表
WATCHLIST = ["RDW", "RCAT", "PLTR", "TSLA", "NVDA", "AMD", "AAPL"]

# --- 1. 连接 Notion ---
# 确保你在 GitHub Settings -> Secrets -> Actions 里配置了这两个变量
notion = Client(auth=os.environ.get("NOTION_TOKEN"))
database_id = os.environ.get("NOTION_DATABASE_ID")

def calculate_max_pain(stock, current_price):
    """计算期权最大痛点 (简化版)"""
    try:
        options_dates = stock.options
        if not options_dates:
            return None
        
        # 选最近的一个到期日
        chain = stock.option_chain(options_dates[0])
        calls = chain.calls
        puts = chain.puts
        
        # 寻找 Open Interest (持仓量) 最大的价位作为参考
        top_call_oi = calls.sort_values('openInterest', ascending=False).iloc[0]['strike']
        top_put_oi = puts.sort_values('openInterest', ascending=False).iloc[0]['strike']
        
        return round((top_call_oi + top_put_oi) / 2, 2)
    except Exception as e:
        print(f"⚠️ {stock.ticker} 期权数据获取失败: {e}")
        return None

def get_stock_data(ticker):
    """获取股票数据和技术指标"""
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
    
    # 计算最大痛点
    max_pain = calculate_max_pain(stock, current_price)
    
    return {
        "price": round(current_price, 2),
        "volume_ratio": round(volume / avg_volume, 1),
        "trend": trend_status,
        "max_pain": max_pain,
        "breakout": is_breakout
    }

def push_to_notion(ticker, data):
    """将结果写入 Notion"""
    # 1. 确定标签 (Multi-select 属性)
    tags = []
    if data['breakout']:
        tags.append({"name": "L3-核心池"})
    elif data['trend'] == "牛市":
        tags.append({"name": "L2-观察池"})
    else:
        tags.append({"name": "L1-初选池"})
        
    # 2. 计算风险警报文字
    alert_text = ""
    if data['max_pain']:
        pain_diff = (data['price'] - data['max_pain']) / data['max_pain']
        if abs(pain_diff) > 0.15: # 偏离痛点 15% 以上
            alert_text = f"⚠️ 偏离痛点 {pain_diff:.1%}"
            tags.append({"name": "风险警报"})

    # 3. 动态构建正文内容 (关键修复点：解决空文字报错)
    rich_text_content = [
        {"text": {"content": f"💰 现价: ${data['price']}\n"}},
        {"text": {"content": f"📊 量比: {data['volume_ratio']}x {'(放量突破)' if data['breakout'] else ''}\n"}},
        {"text": {"content": f"🎯 Max Pain: ${data['max_pain'] if data['max_pain'] else '无数据'}\n"}}
    ]
    
    # 只有当警报文字不为空时，才添加这段红色文字
    if alert_text:
        rich_text_content.append({
            "text": {"content": alert_text},
            "annotations": {"color": "red"}
        })

    # 4. 执行推送
    notion.pages.create(
        parent={"database_id": database_id},
        properties={
            "Name": {"title": [{"text": {"content": ticker}}]},
            "Tags": {"multi_select": tags}
        },
        children=[
            {
                "object": "block",
                "type": "paragraph",
                "paragraph": {
                    "rich_text": rich_text_content
                }
            }
        ]
    )
    print(f"✅ {ticker} 数据已成功推送至 Notion！")

# --- 主程序 ---
if __name__ == "__main__":
    print("🚀 开始执行每日选股任务...")
    success_count = 0
    
    for ticker in WATCHLIST:
        try:
            data = get_stock_data(ticker)
            if data:
                push_to_notion(ticker, data)
                success_count += 1
        except Exception as e:
            print(f"❌ 处理 {ticker} 时出错: {e}")
    
    print(f"\n🏁 任务完成！成功推送 {success_count}/{len(WATCHLIST)} 个股票。")
