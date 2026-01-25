import os
import json
import time
import yfinance as yf
from notion_client import Client # 👈 确保引用的是这个库 
from datetime import datetime, timedelta

# --- 配置 ---
WATCHLIST = ["RDW", "RCAT", "PLTR", "TSLA", "NVDA", "AMD", "AAPL"]
if os.path.exists("stocks.txt"):
    with open("stocks.txt", "r") as f:
        WATCHLIST = list(set([l.strip().upper() for l in f if l.strip()]))

notion = Client(auth=os.environ.get("NOTION_TOKEN"))
database_id = os.environ.get("NOTION_DATABASE_ID")

# --- 加载数据 ---
FLOAT_DB = {}
if os.path.exists("float_data.json"):
    try:
        with open("float_data.json", "r") as f: FLOAT_DB = json.load(f)
    except: pass

# --- 核心逻辑：智能同步 (参考PDF高级方案) ---
def sync_notion_data():
    print("🚀 开始执行智能同步...")
    
    # 1. 获取 Notion 中现有的所有页面 [cite: 35-43]
    print("📋 [1/3] 正在扫描现有数据库...")
    existing_pages = {} # 格式: {"RDW": "page_id_123", "AMD": "page_id_456"}
    
    has_more = True
    start_cursor = None
    
    while has_more:
        try:
            resp = notion.databases.query(
                database_id=database_id, 
                start_cursor=start_cursor, 
                page_size=100
            )
            for page in resp.get("results", []):
                # 提取标题
                ticker = ""
                for prop in page["properties"].values():
                    if prop["type"] == "title" and prop["title"]:
                        ticker = prop["title"][0]["text"]["content"].upper()
                        break
                
                if ticker:
                    # 如果有重复的，先把旧的删了，只留一个ID
                    if ticker in existing_pages:
                        notion.pages.update(page_id=page["id"], archived=True)
                    else:
                        existing_pages[ticker] = page["id"]
                        
            has_more = resp.get("has_more")
            start_cursor = resp.get("next_cursor")
        except Exception as e:
            print(f"❌ 扫描失败: {e} (请检查 YML 是否安装了 notion-client)")
            return

    # 2. 逐个处理清单里的股票 [cite: 79]
    print(f"🔄 [2/3] 正在处理 {len(WATCHLIST)} 只股票...")
    processed_tickers = []
    
    for ticker in WATCHLIST:
        data = get_stock_data(ticker) # 获取数据
        if not data: continue
        
        processed_tickers.append(ticker)
        
        # 构造 Notion 内容属性
        cst_time = (datetime.utcnow() - timedelta(hours=6)).strftime("%m-%d %H:%M CST")
        tags = [{"name": data['status']}]
        if data['alert']: tags.append({"name": "🚨极端换手", "color": "red"})
        
        props = {
            "Name": {"title": [{"text": {"content": ticker}}]},
            "Status": {"select": {"name": data['status']}},
            "Tags": {"multi_select": tags}
        }
        
        # 构造正文内容
        text_blocks = [
            {"object": "block", "type": "paragraph", "paragraph": {"rich_text": [
                {"type": "text", "text": {"content": f"💰 现价: ${data['price']} | 🛡️ 止损: ${data['stop']}\n"}},
                {"type": "text", "text": {"content": f"📊 换手: {data['turnover']}% | 量比: {data['vol']}x\n"}},
                {"type": "text", "text": {"content": f"ℹ️ 源: {data['source']} | 🕒 {cst_time}\n"}, "annotations": {"color": "gray", "italic": True}},
                {"type": "text", "text": {"content": f"{data['alert_msg']}" if data['alert'] else ""}, "annotations": {"color": "red"}}
            ]}}
        ]

        # --- 分支判断：更新 vs 创建 [cite: 80-89] ---
        if ticker in existing_pages:
            # 存在 -> 更新 (Update)
            page_id = existing_pages[ticker]
            try:
                notion.pages.update(page_id=page_id, properties=props)
                # 刷新正文：先清空再添加
                children = notion.blocks.children.list(block_id=page_id).get("results", [])
                for block in children: notion.blocks.delete(block_id=block["id"])
                notion.blocks.children.append(block_id=page_id, children=text_blocks)
                print(f"   ✅ 更新: {ticker}")
            except Exception as e: print(f"   ❌ 更新失败 {ticker}: {e}")
        else:
            # 不存在 -> 创建 (Create)
            try:
                notion.pages.create(parent={"database_id": database_id}, properties=props, children=text_blocks)
                print(f"   ✨ 新建: {ticker}")
            except Exception as e: print(f"   ❌ 新建失败 {ticker}: {e}")

    # 3. 清理不在清单里的废弃股票 
    print("🧹 [3/3] 清理废弃数据...")
    for ticker, page_id in existing_pages.items():
        if ticker not in processed_tickers:
            try:
                notion.pages.update(page_id=page_id, archived=True) # 归档即删除 [cite: 45]
                print(f"   🗑️ 已删除废弃股票: {ticker}")
            except: pass

    print("🏁 同步完成！")

# --- 辅助：获取股票数据 (保持不变) ---
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
