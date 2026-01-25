import os
import json
import time
import yfinance as yf
from notion_client import Client
from datetime import datetime, timedelta

# --- 1. 基础配置 ---
WATCHLIST = ["RDW", "RCAT", "PLTR", "TSLA", "NVDA", "AMD", "AAPL"]
if os.path.exists("stocks.txt"):
    with open("stocks.txt", "r") as f:
        WATCHLIST = [l.strip().upper() for l in f if l.strip()]

notion = Client(auth=os.environ.get("NOTION_TOKEN"))
database_id = os.environ.get("NOTION_DATABASE_ID")

# --- 2. 加载本地数据库 ---
FLOAT_DB = {}
if os.path.exists("float_data.json"):
    try:
        with open("float_data.json", "r") as f:
            FLOAT_DB = json.load(f)
        print(f"📘 已加载本地数据库: {len(FLOAT_DB)} 条记录")
    except Exception as e:
        print(f"⚠️ 读取 float_data.json 失败: {e}")

# --- 3. 辅助功能：清理 Notion 重复项 ---
def clean_and_map_database():
    print("🧹 [系统] 正在扫描数据库索引...")
    ticker_map = {} 
    try:
        all_pages = []
        has_more = True
        start_cursor = None
        while has_more:
            try:
                resp = notion.databases.query(database_id=database_id, start_cursor=start_cursor, page_size=100)
                all_pages.extend(resp.get("results", []))
                has_more = resp.get("has_more")
                start_cursor = resp.get("next_cursor")
            except:
                has_more = False
        
        seen = {}
        duplicates = []
        for page in all_pages:
            ticker = None
            for prop in page["properties"].values():
                if prop["type"] == "title" and prop["title"]:
                    ticker = prop["title"][0]["text"]["content"].upper()
                    break
            if ticker:
                if ticker in seen: duplicates.append(page["id"])
                else:
                    seen[ticker] = page["id"]
                    ticker_map[ticker] = page["id"]
        
        for dup_id in duplicates:
            try: notion.pages.update(page_id=dup_id, archived=True)
            except: pass
        if duplicates: print(f"   🗑️ 已清理 {len(duplicates)} 个重复条目")
            
    except Exception as e:
        print(f"⚠️ 吸尘器跳过: {e}")
    return ticker_map

PAGE_MAP = clean_and_map_database()

# --- 4. 辅助功能：清空页面旧内容 ---
def clear_page_content(page_id):
    try:
        blocks = notion.blocks.children.list(block_id=page_id)
        for block in blocks.get("results", []):
            notion.blocks.delete(block_id=block["id"])
    except: pass

def get_stock_logic(ticker):
    print(f"🔍 深度扫描: {ticker}...")
    try:
        stock = yf.Ticker(ticker)
        hist = stock.history(period="5d") 
        if hist.empty: 
            print(f"   ⚠️ {ticker} 无交易数据")
            return None

        price = round(hist['Close'].iloc[-1], 2)
        open_p = hist['Open'].iloc[-1]
        volume = hist['Volume'].iloc[-1]
        ma_close = hist['Close'].mean()

        # --- 核心：数据源判定 ---
        share_float = 0
        data_source = "❓未知"
        
        # 1. 优先：本地库
        if ticker in FLOAT_DB:
            share_float = FLOAT_DB[ticker]
            data_source = "🔥本地库"
            
        # 2. 备用：Yahoo
        if not share_float:
            share_float = stock.info.get('floatShares')
            data_source = "⚠️YahooAPI"
            
        # 3. 兜底
        if not share_float:
            share_float = stock.info.get('sharesOutstanding')
            data_source = "⚠️总股本"

        # 打印来源
        print(f"   📊 股本: {share_float/1000000:.2f}M (来源: {data_source})")

        turnover_rate = (volume / share_float) if share_float else 0
        avg_vol = hist['Volume'].mean()
        vol_ratio = round(volume / avg_vol, 1) if avg_vol > 0 else 0
        
        # --- 严选评级 ---
        status = "L2-观察池"
        if price > ma_close: status = "L1-初选池"
        if vol_ratio > 2.0 and price > open_p: status = "L3-核心池"

        is_red_alert = False
        alert_msg = ""
        if turnover_rate > 0.5:
            is_red_alert = True
            alert_msg = f"🚨 高换手 ({turnover_rate:.1%})"
            status = "L1-初选池" # 风险降级

        atr = (hist['High'] - hist['Low']).mean()
        stop_loss = round(price - (2.5 * atr), 2)

        return {
            "price": price, 
            "status": status, 
            "stop": stop_loss, 
            "vol": vol_ratio, 
            "turnover": round(turnover_rate * 100, 2),
            "alert": is_red_alert,
            "alert_msg": alert_msg,
            "source": data_source
        }
    except Exception as e:
        print(f"❌ {ticker} 计算出错: {e}")
        return None

def update_notion(ticker, data):
    try:
        # 美中时间 (CST)
        cst_time = datetime.utcnow() - timedelta(hours=6)
        time_str = cst_time.strftime("%m-%d %H:%M CST")
        
        page_id = PAGE_MAP.get(ticker)
        tags = [{"name": data['status']}]
        if data['alert']: tags.append({"name": "🚨极端换手", "color": "red"})
        
        properties = {
            "Name": {"title": [{"text": {"content": ticker}}]},
            "Status": {"select": {"name": data['status']}},
            "Tags": {"multi_select": tags}
        }
        
        # --- 修复点：严格构造 rich_text ---
        # 必须把 type, text, annotations 分开写，不能嵌套错
        text_parts = [
            {
                "type": "text",
                "text": {"content": f"💰 现价: ${data['price']} | 🛡️ 止损: ${data['stop']}\n"},
            },
            {
                "type": "text",
                "text": {"content": f"📊 换手: {data['turnover']}% | 量比: {data['vol']}x\n"},
            },
            {
                "type": "text",
                "text": {"content": f"ℹ️ 源: {data['source']} | 🕒 {time_str}\n"},
                "annotations": {"color": "gray", "italic": True} # ✅ 放在这里才对
            }
        ]
        
        if data['alert']:
            text_parts.append({
                "type": "text",
                "text": {"content": f"{data['alert_msg']}"},
                "annotations": {"color": "red", "bold": True}
            })

        content_block = {
            "object": "block", 
            "type": "paragraph", 
            "paragraph": { "rich_text": text_parts }
        }

        if page_id:
            notion.pages.update(page_id=page_id, properties=properties)
            clear_page_content(page_id) # 先清空
            notion.blocks.children.append(block_id=page_id, children=[content_block])
            print(f"🔄 {ticker} 更新成功")
        else:
            notion.pages.create(
                parent={"database_id": database_id}, 
                properties=properties, 
                children=[content_block]
            )
            print(f"✨ {ticker} 创建成功")
            
    except Exception as e:
        print(f"❌ {ticker} 推送失败: {e}")

if __name__ == "__main__":
    print("🚀 开始执行每日选股任务...")
    for t in WATCHLIST:
        res = get_stock_logic(t)
        if res:
            update_notion(t, res)
    print("🏁 任务完成！")
