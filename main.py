import os
import json
import time
import yfinance as yf
import pandas as pd
from notion_client import Client
from datetime import datetime

# --- 1. 基础配置 ---
WATCHLIST = ["RDW", "RCAT", "PLTR", "TSLA", "NVDA", "AMD", "AAPL"]
# 尝试读取本地清单
if os.path.exists("stocks.txt"):
    with open("stocks.txt", "r") as f:
        WATCHLIST = [l.strip().upper() for l in f if l.strip()]

notion = Client(auth=os.environ.get("NOTION_TOKEN"))
database_id = os.environ.get("NOTION_DATABASE_ID")

# --- 2. 加载本地数据库 (Firecrawl抓取的精准数据) ---
FLOAT_DB = {}
if os.path.exists("float_data.json"):
    try:
        with open("float_data.json", "r") as f:
            FLOAT_DB = json.load(f)
        print(f"📘 已加载本地精准数据: {len(FLOAT_DB)} 条")
    except Exception as e:
        print(f"⚠️ 读取 float_data.json 失败: {e}")

# --- 3. 辅助功能：清理 Notion 重复项 ---
def clean_and_map_database():
    print("🧹 正在扫描数据库，清理重复项...")
    ticker_map = {} 
    
    try:
        all_pages = []
        has_more = True
        start_cursor = None
        while has_more:
            # 如果你的 notion 库版本旧，这里可能会报错，我们加个保险
            try:
                resp = notion.databases.query(database_id=database_id, start_cursor=start_cursor, page_size=100)
                all_pages.extend(resp.get("results", []))
                has_more = resp.get("has_more")
                start_cursor = resp.get("next_cursor")
            except:
                has_more = False # 查不了就跳过
            
        seen = {}
        duplicates = []
        for page in all_pages:
            ticker = None
            for prop in page["properties"].values():
                if prop["type"] == "title" and prop["title"]:
                    ticker = prop["title"][0]["text"]["content"].upper()
                    break
            
            if ticker:
                if ticker in seen:
                    duplicates.append(page["id"])
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

def get_stock_logic(ticker):
    print(f"🔍 深度扫描: {ticker}...")
    try:
        stock = yf.Ticker(ticker)
        hist = stock.history(period="5d") 
        if hist.empty: 
            print(f"⚠️ {ticker} 无法获取K线数据")
            return None

        price = round(hist['Close'].iloc[-1], 2)
        open_p = hist['Open'].iloc[-1]
        low_p = hist['Low'].iloc[-1]
        high_p = hist['High'].iloc[-1]
        volume = hist['Volume'].iloc[-1]
        
        # --- 智能获取流通股 ---
        share_float = 0
        
        # 1. 优先：读本地文件 (RDW=85M)
        if ticker in FLOAT_DB:
            share_float = FLOAT_DB[ticker]
            
        # 2. 备用：Yahoo floatShares
        if not share_float:
            share_float = stock.info.get('floatShares')
            
        # 3. 兜底：Yahoo sharesOutstanding
        if not share_float:
            share_float = stock.info.get('sharesOutstanding')

        turnover_rate = (volume / share_float) if share_float else 0
        
        avg_vol = hist['Volume'].mean()
        vol_ratio = round(volume / avg_vol, 1) if avg_vol > 0 else 0
        ma_close = hist['Close'].mean()
        
        price_pos = (price - low_p) / (high_p - low_p) if (high_p - low_p) != 0 else 0.5
        is_red_alert = False
        alert_msg = ""
        
        if turnover_rate > 0.5 and price_pos < 0.3:
            is_red_alert = True
            alert_msg = f"🚨 警报：天量出货 (换手 {turnover_rate:.1%})"
        elif turnover_rate > 0.6:
            is_red_alert = True
            alert_msg = f"🚨 警报：极端换手 ({turnover_rate:.1%})"

        status = "L1-初选池"
        if price > ma_close: status = "L2-观察池"
        if vol_ratio > 2.0 and price > open_p: status = "L3-核心池"
        if is_red_alert: status = "L1-初选池"

        atr = (hist['High'] - hist['Low']).mean()
        stop_loss = round(price - (2.5 * atr), 2)

        return {
            "price": price, 
            "status": status, 
            "stop": stop_loss, 
            "vol": vol_ratio, 
            "turnover": round(turnover_rate * 100, 2),
            "alert": is_red_alert,
            "alert_msg": alert_msg
        }
    except Exception as e:
        print(f"❌ {ticker} 计算出错: {e}")
        return None

def update_notion(ticker, data):
    try:
        time_str = datetime.now().strftime("%m-%d %H:%M")
        page_id = PAGE_MAP.get(ticker)

        tags = [{"name": data['status']}]
        if data['alert']: tags.append({"name": "🚨极端换手", "color": "red"})
        
        properties = {
            "Name": {"title": [{"text": {"content": ticker}}]},
            "Status": {"select": {"name": data['status']}},
            "Tags": {"multi_select": tags}
        }
        
        # --- 📝 关键修正：这里修复了 annotations 的位置错误 ---
        content_block = {
            "object": "block", 
            "type": "paragraph", 
            "paragraph": {
                "rich_text": [
                    {
                        "type": "text",
                        "text": {"content": f"💰 现价: ${data['price']} | 🛡️ 止损: ${data['stop']}\n"}
                    },
                    {
                        "type": "text",
                        "text": {"content": f"📊 换手: {data['turnover']}% | 量比: {data['vol']}x\n"}
                    },
                    {
                        "type": "text",
                        "text": {"content": f"🕒 更新: {time_str}  "},
                        "annotations": {"color": "gray", "italic": True}  # 👈 现在它在外面了，Notion 会开心的
                    },
                    {
                        "type": "text",
                        "text": {"content": f"{data['alert_msg']}" if data['alert'] else ""},
                        "annotations": {"color": "red"}
                    }
                ]
            }
        }

        if page_id:
            notion.pages.update(page_id=page_id, properties=properties)
            # 尝试追加内容
            try: notion.blocks.children.append(block_id=page_id, children=[content_block])
            except: pass
            print(f"🔄 {ticker} 更新成功")
        else:
            notion.pages.create(
                parent={"database_id": database_id}, 
                properties=properties, 
                children=[content_block]
            )
            print(f"✨ {ticker} 创建成功")
            
    except Exception as e:
        # 这里会打印详细错误，如果还报错请截图这里
        print(f"❌ {ticker} 推送 Notion 最终失败: {e}")

if __name__ == "__main__":
    print("🚀 开始执行每日选股任务...")
    
    for t in WATCHLIST:
        res = get_stock_logic(t)
        if res:
            update_notion(t, res)
            
    print("🏁 任务完成！")
