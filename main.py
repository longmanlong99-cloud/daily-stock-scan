import os
import json
import time
import requests # 👈 只用这个最稳的库
import yfinance as yf
from datetime import datetime, timedelta

# --- 1. 配置区 ---
WATCHLIST = ["RDW", "RCAT", "PLTR", "TSLA", "NVDA", "AMD", "AAPL"]
if os.path.exists("stocks.txt"):
    with open("stocks.txt", "r") as f:
        WATCHLIST = list(set([l.strip().upper() for l in f if l.strip()]))

# 获取密钥
NOTION_TOKEN = os.environ.get("NOTION_TOKEN")
DATABASE_ID = os.environ.get("NOTION_DATABASE_ID")

# 构造通用的请求头 (模拟浏览器发包)
HEADERS = {
    "Authorization": f"Bearer {NOTION_TOKEN}",
    "Notion-Version": "2022-06-28", # 锁死版本，防止变动
    "Content-Type": "application/json"
}

# --- 2. 加载本地数据 ---
FLOAT_DB = {}
if os.path.exists("float_data.json"):
    try:
        with open("float_data.json", "r") as f: FLOAT_DB = json.load(f)
        print(f"📘 已加载本地数据库: {len(FLOAT_DB)} 条记录")
    except: pass

# --- 3. 核心功能：手写 API 请求 (绕过所有库文件冲突) ---

def notion_api(method, endpoint, payload=None):
    """万能 API 发送器"""
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

def sync_notion_data():
    print("🚀 启动核弹级直连同步 (无依赖版)...")
    
    # 1. 全量扫描 (直接发 POST 请求查库)
    print("📋 [1/3] 扫描 Notion...")
    existing_pages = {}
    has_more = True
    start_cursor = None
    
    while has_more:
        payload = {"page_size": 100}
        if start_cursor: payload["start_cursor"] = start_cursor
        
        # 直接调用 API，不再依赖 query 方法
        data = notion_api("POST", f"/databases/{DATABASE_ID}/query", payload)
        
        for page in data.get("results", []):
            try:
                # 解析标题
                props = page.get("properties", {})
                name_prop = props.get("Name", {}) or props.get("title", {}) # 兼容不同列名
                title_list = name_prop.get("title", [])
                if title_list:
                    ticker = title_list[0]["text"]["content"].upper()
                    # 查重逻辑
                    if ticker in existing_pages:
                        # 归档旧的
                        notion_api("PATCH", f"/pages/{page['id']}", {"archived": True})
                    else:
                        existing_pages[ticker] = page["id"]
            except: pass
            
        has_more = data.get("has_more", False)
        start_cursor = data.get("next_cursor")

    # 2. 处理清单
    print(f"🔄 [2/3] 更新 {len(WATCHLIST)} 只股票...")
    processed_tickers = []
    
    for ticker in WATCHLIST:
        data = get_stock_data(ticker)
        if not data: continue
        processed_tickers.append(ticker)
        
        cst_time = (datetime.utcnow() - timedelta(hours=6)).strftime("%m-%d %H:%M CST")
        
        # 构造属性
        properties = {
            "Name": {"title": [{"text": {"content": ticker}}]},
            "Status": {"select": {"name": data['status']}},
            "Tags": {"multi_select": [{"name": data['status']}] + 
                     ([{"name": "🚨极端换手", "color": "red"}] if data['alert'] else [])}
        }
        
        # 构造正文块
        children_blocks = [
            {"object": "block", "type": "paragraph", "paragraph": {"rich_text": [
                {"type": "text", "text": {"content": f"💰 现价: ${data['price']} | 🛡️ 止损: ${data['stop']}\n"}},
                {"type": "text", "text": {"content": f"📊 换手: {data['turnover']}% | 量比: {data['vol']}x\n"}},
                {"type": "text", "text": {"content": f"ℹ️ 源: {data['source']} | 🕒 {cst_time}\n"}, "annotations": {"color": "gray", "italic": True}},
                {"type": "text", "text": {"content": f"{data['alert_msg']}" if data['alert'] else ""}, "annotations": {"color": "red"}}
            ]}}
        ]

        if ticker in existing_pages:
            # 更新
            page_id = existing_pages[ticker]
            # 更新属性
            notion_api("PATCH", f"/pages/{page_id}", {"properties": properties})
            
            # 清空旧内容 (获取子块 -> 删除)
            blocks = notion_api("GET", f"/blocks/{page_id}/children")
            for block in blocks.get("results", []):
                notion_api("DELETE", f"/blocks/{block['id']}")
            
            # 写入新内容
            notion_api("PATCH", f"/blocks/{page_id}/children", {"children": children_blocks})
            print(f"   ✅ 更新: {ticker}")
        else:
            # 新建
            new_page = {
                "parent": {"database_id": DATABASE_ID},
                "properties": properties,
                "children": children_blocks
            }
            notion_api("POST", "/pages", new_page)
            print(f"   ✨ 新建: {ticker}")

    # 3. 清理
    print("🧹 [3/3] 清理废弃数据...")
    for ticker, page_id in existing_pages.items():
        if ticker not in processed_tickers:
            notion_api("PATCH", f"/pages/{page_id}", {"archived": True})
            print(f"   🗑️ 删除: {ticker}")

    print("🏁 完成！")

# --- 辅助函数 (保持不变) ---
def get_stock_data(ticker):
    try:
        stock = yf.Ticker(ticker)
        hist = stock.history(period="5d")
        if hist.empty: return None
        price = round(hist['Close'].iloc[-1], 2)
        volume = hist['Volume'].iloc[-1]
        
        share_float = 0
        source = "❓"
        if ticker in FLOAT_DB: 
            share_float = FLOAT_DB[ticker]
            source = "🔥本地库"
        elif stock.info.get('floatShares'):
            share_float = stock.info.get('floatShares')
            source = "⚠️Yahoo"
        else:
            share_float = stock.info.get('sharesOutstanding')
            source = "⚠️总股本"

        turnover = (volume / share_float) if share_float else 0
        ma = hist['Close'].mean()
        status = "L1-初选池" if turnover > 0.5 else ("L1-初选池" if price > ma else "L2-观察池")
        atr = (hist['High'] - hist['Low']).mean()
        stop = round(price - 2.5 * atr, 2)
        
        return {
            "price": price, "status": status, "stop": stop,
            "turnover": round(turnover*100, 2), 
            "vol": round(volume/hist['Volume'].mean(), 1) if hist['Volume'].mean() else 0,
            "source": source,
            "alert": turnover > 0.5,
            "alert_msg": f"🚨 高换手 {turnover:.1%}"
        }
    except: return None

if __name__ == "__main__":
    sync_notion_data()
