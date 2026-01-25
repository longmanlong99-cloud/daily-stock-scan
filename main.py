import os
import yfinance as yf
import pandas as pd
from notion_client import Client

# --- 配置 ---
WATCHLIST = ["RDW", "RCAT", "PLTR", "TSLA", "NVDA", "AMD", "AAPL"]
notion = Client(auth=os.environ.get("NOTION_TOKEN"))
database_id = os.environ.get("NOTION_DATABASE_ID")

def get_stock_logic(ticker):
    print(f"🔍 深度扫描: {ticker}...")
    stock = yf.Ticker(ticker)
    hist = stock.history(period="1y")
    if hist.empty: return None

    price = round(hist['Close'].iloc[-1], 2)
    open_p = hist['Open'].iloc[-1]
    low_p = hist['Low'].iloc[-1]
    high_p = hist['High'].iloc[-1]
    
    # 修正：量比计算 (2-3倍就很强了)
    vol_ratio = round(hist['Volume'].iloc[-1] / hist['Volume'].tail(20).mean(), 1)
    ma200 = hist['Close'].tail(200).mean()
    
    # 警报逻辑修正：量比 > 5.0 且 收盘在全天波动的底部 20% 区域
    price_position = (price - low_p) / (high_p - low_p) if (high_p - low_p) != 0 else 0.5
    is_red_alert = vol_ratio > 5.0 and price < open_p and price_position < 0.2

    # 漏斗分级
    status = "L1-初选池"
    if price > ma200: status = "L2-观察池"
    if vol_ratio > 2.0 and price > open_p: status = "L3-核心池"
    
    # 若有风险警报，强制打回初选池
    if is_red_alert: status = "L1-初选池"

    # ATR止损
    atr = (hist['High'] - hist['Low']).rolling(14).mean().iloc[-1]
    stop_loss = round(price - (2.5 * atr), 2)

    return {"price": price, "status": status, "stop": stop_loss, "vol": vol_ratio, "alert": is_red_alert}

def update_notion(ticker, data):
    """修复查询报错问题的更新函数"""
    # 修正后的查询方式
    try:
        query = notion.databases.query(
            **{"database_id": database_id, "filter": {"property": "Name", "title": {"equals": ticker}}}
        ).get("results")
    except Exception as e:
        print(f"⚠️ 查询 {ticker} 失败: {e}")
        return

    tags = [{"name": data['status']}]
    if data['alert']: tags.append({"name": "🚨天量出货", "color": "red"})
    if data['vol'] > 3: tags.append({"name": "高波动/博弈", "color": "orange"})

    props = {
        "Name": {"title": [{"text": {"content": ticker}}]},
        "Status": {"select": {"name": data['status']}},
        "Tags": {"multi_select": tags}
    }
    
    body_content = [
        {"object": "block", "type": "paragraph", "paragraph": {"rich_text": [
            {"text": {"content": f"💰 现价: ${data['price']} | 🛡️ 止损: ${data['stop']}\n"}},
            {"text": {"content": f"📊 量比: {data['vol']}x\n"}},
            {"text": {"content": "🚨 警报：检测到天量阴线，注意出货风险！" if data['alert'] else ""}, "annotations": {"color": "red"}}
        ]}}
    ]

    if query:
        # 更新现有卡片
        notion.pages.update(page_id=query[0]["id"], properties=props)
        print(f"🔄 {ticker} 已更新")
    else:
        # 创建新卡片
        notion.pages.create(parent={"database_id": database_id}, properties=props, children=body_content)
        print(f"✨ {ticker} 已入库")

if __name__ == "__main__":
    for t in WATCHLIST:
        res = get_stock_logic(t)
        if res: update_notion(t, res)
