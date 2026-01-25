import os
import json
import time
import requests
import yfinance as yf
from notion_client import Client

# 配置
POLYGON_KEY = os.environ.get("POLYGON_API_KEY")
NOTION = Client(auth=os.environ.get("NOTION_TOKEN"))
DATABASE_ID = os.environ.get("NOTION_DATABASE_ID")

def load_data():
    # 1. 读股票清单
    watchlist = ["RDW", "RCAT", "PLTR", "TSLA", "NVDA", "AMD", "AAPL"]
    if os.path.exists("stocks.txt"):
        with open("stocks.txt", "r") as f:
            watchlist = [l.strip().upper() for l in f if l.strip()]
            
    # 2. 读流通股数据库 (刚才搬运工生成的那个文件)
    float_db = {}
    if os.path.exists("float_data.json"):
        with open("float_data.json", "r") as f:
            float_db = json.load(f)
    else:
        print("⚠️ 警告：未找到 float_data.json，请先运行 update_floats.py！")
        
    return watchlist, float_db

WATCHLIST, FLOAT_DB = load_data()

def get_analysis(ticker):
    print(f"🔍 极速扫描: {ticker}...")
    
    # 1. 从本地文件拿分母 (0耗时，精准)
    share_float = FLOAT_DB.get(ticker)
    
    # 如果本地没有数据，说明这只股是新加的，还没运行搬运工
    if not share_float:
        print(f"   ❌ 缺失数据，请手动运行 '更新基本面数据' 脚本")
        return None
        
    print(f"   📘 流通股本: {share_float/1000000:.2f}M")

    # 2. 问 Polygon 要成交量 (分子)
    time.sleep(12) # 遵守 API 规则
    poly_volume = 0
    poly_price = 0
    
    try:
        # 获取前一日数据
        url = f"https://api.polygon.io/v2/aggs/ticker/{ticker}/prev?adjusted=true&apiKey={POLYGON_KEY}"
        r = requests.get(url)
        if r.status_code == 200 and r.json().get("resultsCount", 0) > 0:
            res = r.json()["results"][0]
            poly_volume = res.get("v", 0)
            poly_price = res.get("c", 0)
    except: pass

    if poly_volume == 0: return None

    # 3. 计算换手率 (精准！)
    turnover_rate = poly_volume / share_float
    print(f"   📊 换手率: {turnover_rate:.2%}")

    # 4. 辅助数据 (Yahoo 仅用于计算止损和均线)
    ma200 = poly_price
    stop_loss = poly_price * 0.9
    vol_ratio = 1.0
    try:
        hist = yf.Ticker(ticker).history(period="1y")
        if not hist.empty:
            ma200 = hist['Close'].tail(200).mean()
            atr = (hist['High'] - hist['Low']).tail(14).mean()
            stop_loss = round(poly_price - (2.5 * atr), 2)
            avg_vol = hist['Volume'].tail(20).mean()
            vol_ratio = round(poly_volume / avg_vol, 1) if avg_vol > 0 else 0
    except: pass

    # 5. 警报逻辑
    is_red_alert = False
    alert_msg = ""
    if turnover_rate > 0.20:
        is_red_alert = True
        alert_msg = f"🚨 警报：高换手出货 (换手 {turnover_rate:.1%})"

    status = "L1-初选池"
    if not is_red_alert: status = "L2-观察池"

    return {
        "price": poly_price,
        "status": status,
        "stop": stop_loss,
        "vol": vol_ratio,
        "turnover": round(turnover_rate * 100, 2),
        "alert": is_red_alert,
        "alert_msg": alert_msg
    }

def update_notion(ticker, data):
    # 这部分和之前一样，负责推送到 Notion
    page_id = None
    try:
        resp = NOTION.databases.query(
            database_id=DATABASE_ID,
            filter={"property": "Name", "title": {"equals": ticker}}
        )
        if resp.get("results"): page_id = resp["results"][0]["id"]
    except: pass 
    
    tags = [{"name": data['status']}]
    if data['alert']: tags.append({"name": "🚨极端换手", "color": "red"})
    
    props = {
        "Name": {"title": [{"text": {"content": ticker}}]},
        "Status": {"select": {"name": data['status']}},
        "Tags": {"multi_select": tags}
    }
    content = [{"object": "block", "type": "paragraph", "paragraph": {"rich_text": [
            {"text": {"content": f"💰 现价: ${data['price']} | 🛡️ 止损: ${data['stop']:.2f}\n"}},
            {"text": {"content": f"📊 换手: {data['turnover']}% | ⚠️ {data['alert_msg'] if data['alert'] else '正常'}\n"}}
    ]}}]
    try:
        if page_id: NOTION.pages.update(page_id=page_id, properties=props)
        else: NOTION.pages.create(parent={"database_id": DATABASE_ID}, properties=props, children=content)
        print(f"✅ {ticker} 更新成功")
    except Exception as e: print(f"❌ Notion 错误: {e}")

if __name__ == "__main__":
    print("🚀 启动极速扫描 (读取 float_data.json)...")
    for t in WATCHLIST:
        data = get_analysis(t)
        if data:
            update_notion(t, data)
    print("🏁 完成")
