import os
import json
import time
import requests
import yfinance as yf
from notion_client import Client
from datetime import datetime  # 👈 新增：引入时间模块

# --- 配置区 ---
POLYGON_KEY = os.environ.get("POLYGON_API_KEY")
NOTION = Client(auth=os.environ.get("NOTION_TOKEN"))
DATABASE_ID = os.environ.get("NOTION_DATABASE_ID")

def load_data():
    """读取清单和本地数据库"""
    # 1. 股票清单
    watchlist = ["RDW", "RCAT", "PLTR", "TSLA", "NVDA", "AMD", "AAPL"]
    if os.path.exists("stocks.txt"):
        with open("stocks.txt", "r") as f:
            watchlist = [l.strip().upper() for l in f if l.strip()]
            
    # 2. 流通股本地库 (由 update_floats.py 生成)
    float_db = {}
    if os.path.exists("float_data.json"):
        with open("float_data.json", "r") as f:
            float_db = json.load(f)
    else:
        print("⚠️ 提示：未找到 float_data.json，将使用自动查询模式")
        
    return watchlist, float_db

WATCHLIST, FLOAT_DB = load_data()

# --- 🧹 核心功能：数据库自动吸尘器 ---
def clean_and_map_database():
    print("🧹 正在扫描 Notion 数据库，清理重复项...")
    
    all_pages = []
    has_more = True
    start_cursor = None
    
    while has_more:
        try:
            resp = NOTION.databases.query(
                database_id=DATABASE_ID,
                start_cursor=start_cursor,
                page_size=100
            )
            all_pages.extend(resp.get("results", []))
            has_more = resp.get("has_more")
            start_cursor = resp.get("next_cursor")
        except Exception as e:
            print(f"❌ 读取 Notion 失败: {e}")
            return {}

    ticker_map = {} 
    duplicates = [] 
    seen_tickers = {}
    
    for page in all_pages:
        ticker = None
        page_id = page["id"]
        
        for prop_name, prop_val in page["properties"].items():
            if prop_val["type"] == "title":
                if prop_val["title"]:
                    ticker = prop_val["title"][0]["text"]["content"].upper()
                break
        
        if ticker:
            if ticker in seen_tickers:
                duplicates.append(page_id)
            else:
                seen_tickers[ticker] = page_id
                ticker_map[ticker] = page_id

    if duplicates:
        print(f"   ⚠️ 发现 {len(duplicates)} 个重复卡片，正在自动删除...")
        for dup_id in duplicates:
            try:
                NOTION.pages.update(page_id=dup_id, archived=True)
                print(f"      🗑️ 已清理重复ID: {dup_id}")
            except: pass
    else:
        print("   ✅ 数据库很干净，没有重复项。")
        
    return ticker_map

# 启动时先运行一次清理
PAGE_MAP = clean_and_map_database()

def get_analysis(ticker):
    print(f"🔍 分析: {ticker}...")
    
    # 1. 确定流通股本
    share_float = FLOAT_DB.get(ticker)
    
    if not share_float:
        try:
            # 兜底逻辑
            import requests
            stock = yf.Ticker(ticker)
            share_float = stock.info.get('floatShares')
        except: pass
    
    if share_float:
        print(f"   📘 流通股本: {share_float/1000000:.2f}M")
    else:
        print("   ⚠️ 无法获取股本，将跳过换手率计算")

    # 2. 确定成交量 (Polygon)
    time.sleep(12) 
    poly_volume = 0
    poly_price = 0
    
    try:
        url = f"https://api.polygon.io/v2/aggs/ticker/{ticker}/prev?adjusted=true&apiKey={POLYGON_KEY}"
        r = requests.get(url)
        if r.status_code == 200 and r.json().get("resultsCount", 0) > 0:
            res = r.json()["results"][0]
            poly_volume = res.get("v", 0)
            poly_price = res.get("c", 0)
    except: pass

    if poly_volume == 0: return None

    # 3. 计算
    turnover_rate = 0
    if share_float:
        turnover_rate = poly_volume / share_float
    print(f"   📊 换手率: {turnover_rate:.2%}")

    # 4. 辅助数据
    ma200 = poly_price
    stop_loss = poly_price * 0.9
    vol_ratio = 1.0
    try:
        hist = yf.Ticker(ticker).history(period="1y")
        if not hist.empty:
            ma200 = hist['Close'].tail(200).mean()
            avg_vol = hist['Volume'].tail(20).mean()
            vol_ratio = round(poly_volume / avg_vol, 1) if avg_vol > 0 else 0
            atr = (hist['High'] - hist['Low']).tail(14).mean()
            stop_loss = round(poly_price - (2.5 * atr), 2)
    except: pass

    # 5. 警报
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
    page_id = PAGE_MAP.get(ticker)
    
    # 👇 获取当前时间 (UTC时间)
    now_str = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
    
    tags = [{"name": data['status']}]
    if data['alert']: tags.append({"name": "🚨极端换手", "color": "red"})
    
    props = {
        "Name": {"title": [{"text": {"content": ticker}}]},
        "Status": {"select": {"name": data['status']}},
        "Tags": {"multi_select": tags}
    }
    
    # 👇 内容区增加了时间显示
    content = [{"object": "block", "type": "paragraph", "paragraph": {"rich_text": [
            {"text": {"content": f"💰 现价: ${data['price']} | 🛡️ 止损: ${data['stop']:.2f}\n"}},
            {"text": {"content": f"📊 换手: {data['turnover']}% | ⚠️ {data['alert_msg'] if data['alert'] else '正常'}\n"}},
            {"text": {"content": f"🕒 更新: {now_str}", "annotations": {"color": "gray"}}}
    ]}}]
    
    try:
        if page_id:
            NOTION.pages.update(page_id=page_id, properties=props)
            print(f"✅ {ticker} 更新成功 (ID: {page_id})")
        else:
            new_page = NOTION.pages.create(parent={"database_id": DATABASE_ID}, properties=props, children=content)
            print(f"✨ {ticker} 新建成功")
            PAGE_MAP[ticker] = new_page["id"]
    except Exception as e: print(f"❌ Notion 错误: {e}")

if __name__ == "__main__":
    print("🚀 启动 (吸尘器+时间戳版)...")
    for t in WATCHLIST:
        data = get_analysis(t)
        if data:
            update_notion(t, data)
    print("🏁 完成")
