import os
import yfinance as yf
import pandas as pd
from notion_client import Client

# --- 配置：GitHub Secrets 里的变量 ---
WATCHLIST = ["RDW", "RCAT", "PLTR", "TSLA", "NVDA", "AMD", "AAPL"]
notion = Client(auth=os.environ.get("NOTION_TOKEN"))
database_id = os.environ.get("NOTION_DATABASE_ID")

def get_stock_logic(ticker):
    print(f"🔍 深度扫描: {ticker}...")
    stock = yf.Ticker(ticker)
    hist = stock.history(period="1y")
    if hist.empty: return None

    # 获取基础数据
    price = round(hist['Close'].iloc[-1], 2)
    open_p = hist['Open'].iloc[-1]
    low_p = hist['Low'].iloc[-1]
    high_p = hist['High'].iloc[-1]
    
    # 计算量比 (当日成交量 / 过去20日平均)
    vol_ratio = round(hist['Volume'].iloc[-1] / hist['Volume'].tail(20).mean(), 1)
    ma200 = hist['Close'].tail(200).mean()
    
    # 1. 风险判定逻辑 (针对 RDW 这种情况)
    is_red_alert = False
    # 判断收盘价是否在全天波动的底部 (低于20%的位置)
    price_position = (price - low_p) / (high_p - low_p) if (high_p - low_p) != 0 else 0.5
    
    # 规则：量比极大 且 收阴线 且 收到最低位附近 (天量出货)
    if vol_ratio > 30.0 and price < open_p and price_position < 0.2:
        is_red_alert = True

    # 2. 漏斗分级逻辑
    status = "L1-初选池"
    if price > ma200: 
        status = "L2-观察池"
    if vol_ratio > 2.0 and price > open_p: 
        status = "L3-核心池"
    
    # 如果是风险警报，强制降级到 L1
    if is_red_alert: status = "L1-初选池"

    # 3. ATR 动态止损计算
    atr = (hist['High'] - hist['Low']).rolling(14).mean().iloc[-1]
    stop_loss = round(price - (2.5 * atr), 2)

    return {
        "price": price, 
        "status": status, 
        "stop": stop_loss, 
        "vol": vol_ratio, 
        "alert": is_red_alert
    }

def update_notion(ticker, data):
    """更新 Notion 数据并去重"""
    # 查找是否已有该股票
    query = notion.databases.query(
        database_id=database_id,
        filter={"property": "Name", "title": {"equals": ticker}}
    ).get("results")

    # 准备卡片属性
    tags = [{"name": data['status']}]
    if data['alert']: tags.append({"name": "🚨天量出货", "color": "red"})
    if data['vol'] > 3: tags.append({"name": "高波动/博弈", "color": "orange"})

    props = {
        "Name": {"title": [{"text": {"content": ticker}}]},
        "Status": {"select": {"name": data['status']}},
        "Tags": {"multi_select": tags}
    }
    
    # 卡片详情内容
    rich_text = [{"object": "block", "type": "paragraph", "paragraph": {"rich_text": [
        {"text": {"content": f"💰 现价: ${data['price']} | 🛡️ 止损: ${data['stop']}\n"}},
        {"text": {"content": f"📊 量比: {data['vol']}x\n"}},
        {"text": {"content": "🚨 警报：检测到天量阴线，注意避险！" if data['alert'] else ""}, "annotations": {"color": "red"}}
    ]}}]

    if query:
        # 如果存在，更新旧卡片
        notion.pages.update(page_id=query[0]["id"], properties=props)
        print(f"🔄 {ticker} 数据已更新")
    else:
        # 如果不存在，创建新卡片
        notion.pages.create(parent={"database_id": database_id}, properties=props, children=rich_text)
        print(f"✨ {ticker} 已成功入库")

if __name__ == "__main__":
    for t in WATCHLIST:
        try:
            res = get_stock_logic(t)
            if res: update_notion(t, res)
        except Exception as e:
            print(f"❌ {t} 处理失败: {e}")
