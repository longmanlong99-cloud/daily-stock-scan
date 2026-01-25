import os
import json
import time
import requests
from datetime import datetime, timedelta

# --- 1. 配置区 ---
WATCHLIST = ["RDW", "RCAT", "PLTR", "TSLA", "NVDA", "AMD", "AAPL"]
if os.path.exists("stocks.txt"):
    with open("stocks.txt", "r") as f:
        WATCHLIST = list(set([l.strip().upper() for l in f if l.strip()]))

NOTION_TOKEN = os.environ.get("NOTION_TOKEN")
DATABASE_ID = os.environ.get("NOTION_DATABASE_ID")

HEADERS = {
    "Authorization": f"Bearer {NOTION_TOKEN}",
    "Notion-Version": "2022-06-28",
    "Content-Type": "application/json"
}

# --- 2. 加载本地数据 (双数据源) ---
# ### CHANGED HERE ###
FLOAT_DB = {}
if os.path.exists("float_data.json"):
    try:
        with open("float_data.json", "r") as f: FLOAT_DB = json.load(f)
    except: pass

DAILY_CACHE = {}
if os.path.exists("daily_cache.json"):
    try:
        with open("daily_cache.json", "r") as f: DAILY_CACHE = json.load(f)
        print(f"📗 已加载行情缓存: {len(DAILY_CACHE)} 条")
    except: pass

# --- 3. 核心功能 ---

def notion_api(method, endpoint, payload=None):
    url = f"https://api.notion.com/v1{endpoint}"
    try:
        if method == "POST":
            resp = requests.post(url, headers=HEADERS, json=payload, timeout=20)
        elif method == "PATCH":
            resp = requests.patch(url, headers=HEADERS, json=payload, timeout=20)
        elif method == "DELETE":
            resp = requests.delete(url, headers=HEADERS, timeout=20)
        else:
            resp = requests.get(url, headers=HEADERS, timeout=20)
        return resp.json()
    except Exception as e:
        print(f"⚠️ API 请求异常: {e}")
        return {}

def get_stock_data(ticker):
    # ### CHANGED HERE: 不再请求 Yahoo，直接读缓存 ###
    if ticker not in DAILY_CACHE:
        return None
    
    market_data = DAILY_CACHE[ticker]
    
    # 获取流通股
    share_float = FLOAT_DB.get(ticker, 0)
    source = "🔥本地库" if ticker in FLOAT_DB else "⚠️未知"
    
    # 基础数据
    price = market_data['price']
    volume = market_data['volume']
    max_pain = market_data.get('max_pain')
    ma200 = market_data.get('ma200', price)
    
    # 计算换手
    turnover = (volume / share_float) if share_float else 0
    
    # --- PDF 筛选逻辑 ---
    status = "L1-初选池"
    alert = False
    alert_msg = ""
    
    # L2 判断: 价格在年线之上 OR 放量突破
    if price > ma200 or market_data.get('vol_ratio', 0) > 2.0:
        status = "L2-观察池"
        
    # L3/报警 判断: 痛点偏离 (PDF 第6页)
    if max_pain:
        deviation = abs(price - max_pain) / max_pain
        if deviation > 0.2:
            status = "L3-核心池"
            alert = True
            alert_msg = f"⚡ 偏离痛点 {deviation:.0%} (Pain:${max_pain})"
            
    # 极端换手报警
    if turnover > 0.2:
        alert = True
        alert_msg = f"🚨 极端换手 {turnover:.1%}"

    # 动态止损 (MA200 下方 5%)
    stop = round(ma200 * 0.95, 2)

    return {
        "price": price, "status": status, "stop": stop,
        "turnover": round(turnover*100, 2), 
        "vol": market_data.get('vol_ratio', 0),
        "source": source,
        "alert": alert,
        "alert_msg": alert_msg,
        "max_pain": max_pain
    }

def sync_notion_data():
    print("🚀 启动 Notion 同步 (读取本地缓存版)...")
    
    # 1. 扫描 Notion (保持原样)
    print("📋 [1/3] 扫描 Notion...")
    existing_pages = {}
    has_more = True
    start_cursor = None
    
    while has_more:
        payload = {"page_size": 100}
        if start_cursor: payload["start_cursor"] = start_cursor
        data = notion_api("POST", f"/databases/{DATABASE_ID}/query", payload)
        
        for page in data.get("results", []):
            try:
                props = page.get("properties", {})
                name_prop = props.get("Name", {}) or props.get("title", {})
                title_list = name_prop.get("title", [])
                if title_list:
                    ticker = title_list[0]["text"]["content"].upper()
                    if ticker in existing_pages:
                        notion_api("PATCH", f"/pages/{page['id']}", {"archived": True})
                    else:
                        existing_pages[ticker] = page["id"]
            except: pass
        has_more = data.get("has_more", False)
        start_cursor = data.get("next_cursor")

    # 2. 更新清单
    print(f"🔄 [2/3] 更新 {len(WATCHLIST)} 只股票...")
    processed_tickers = []
    
    for ticker in WATCHLIST:
        data = get_stock_data(ticker)
        if not data: continue
        processed_tickers.append(ticker)
        
        cst_time = (datetime.utcnow() - timedelta(hours=6)).strftime("%m-%d %H:%M CST")
        
        properties = {
            "Name": {"title": [{"text": {"content": ticker}}]},
            "Status": {"select": {"name": data['status']}},
            "Tags": {"multi_select": [{"name": data['status']}] + 
                     ([{"name": "🚨警报", "color": "red"}] if data['alert'] else [])}
        }
        
        # ### CHANGED HERE: 增加 Max Pain 显示 ###
        pain_text = f" | 🎯 痛点: ${data['max_pain']}" if data['max_pain'] else ""
        
        children_blocks = [
            {"object": "block", "type": "paragraph", "paragraph": {"rich_text": [
                {"type": "text", "text": {"content": f"💰 现价: ${data['price']} | 🛡️ 趋势线: ${data['stop']}{pain_text}\n"}},
                {"type": "text", "text": {"content": f"📊 换手: {data['turnover']}% | 量比: {data['vol']}x\n"}},
                {"type": "text", "text": {"content": f"ℹ️ 源: {data['source']} | 🕒 {cst_time}\n"}, "annotations": {"color": "gray", "italic": True}},
                {"type": "text", "text": {"content": f"{data['alert_msg']}" if data['alert'] else ""}, "annotations": {"color": "red"}}
            ]}}
        ]

        if ticker in existing_pages:
            page_id = existing_pages[ticker]
            notion_api("PATCH", f"/pages/{page_id}", {"properties": properties})
            blocks = notion_api("GET", f"/blocks/{page_id}/children")
            for block in blocks.get("results", []):
                notion_api("DELETE", f"/blocks/{block['id']}")
            notion_api("PATCH", f"/blocks/{page_id}/children", {"children": children_blocks})
            print(f"   ✅ 更新: {ticker}")
        else:
            new_page = {
                "parent": {"database_id": DATABASE_ID},
                "properties": properties,
                "children": children_blocks
            }
            notion_api("POST", "/pages", new_page)
            print(f"   ✨ 新建: {ticker}")

    # 3. 清理 (保持原样)
    print("🧹 [3/3] 清理废弃数据...")
    for ticker, page_id in existing_pages.items():
        if ticker not in processed_tickers:
            notion_api("PATCH", f"/pages/{page_id}", {"archived": True})
            print(f"   🗑️ 删除: {ticker}")

    print("🏁 完成！")

if __name__ == "__main__":
    sync_notion_data()

