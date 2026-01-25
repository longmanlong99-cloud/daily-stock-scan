import os
import json
import time
import yfinance as yf
import pandas as pd
from notion_client import Client
import notion_client
from datetime import datetime

# --- 1. 基础配置 ---
# 优先读取 stocks.txt，如果没有则使用默认列表
WATCHLIST = ["RDW", "RCAT", "PLTR", "TSLA", "NVDA", "AMD", "AAPL"]
if os.path.exists("stocks.txt"):
    with open("stocks.txt", "r") as f:
        WATCHLIST = [l.strip().upper() for l in f if l.strip()]

# 读取之前搬运工(Firecrawl)抓下来的精准数据
FLOAT_DB = {}
if os.path.exists("float_data.json"):
    with open("float_data.json", "r") as f:
        try: FLOAT_DB = json.load(f)
        except: pass

notion = Client(auth=os.environ.get("NOTION_TOKEN"))
database_id = os.environ.get("NOTION_DATABASE_ID")

# --- 🧹 智能吸尘器：启动时清理重复项 ---
def clean_and_map_database():
    print("🧹 正在扫描数据库，清理重复项...")
    ticker_map = {} # {'RDW': 'page_id'}
    
    try:
        all_pages = []
        has_more = True
        start_cursor = None
        while has_more:
            resp = notion.databases.query(database_id=database_id, start_cursor=start_cursor, page_size=100)
            all_pages.extend(resp.get("results", []))
            has_more = resp.get("has_more")
            start_cursor = resp.get("next_cursor")
            
        seen = {}
        duplicates = []
        for page in all_pages:
            ticker = None
            # 提取标题
            for prop in page["properties"].values():
                if prop["type"] == "title" and prop["title"]:
                    ticker = prop["title"][0]["text"]["content"].upper()
                    break
            
            if ticker:
                if ticker in seen:
                    duplicates.append(page["id"]) # 标记重复
                else:
                    seen[ticker] = page["id"]
                    ticker_map[ticker] = page["id"]
                    
        # 执行删除
        for dup_id in duplicates:
            try: notion.pages.update(page_id=dup_id, archived=True)
            except: pass
        if duplicates: print(f"   🗑️ 已清理 {len(duplicates)} 个重复条目")
            
    except Exception as e:
        print(f"⚠️ 吸尘器跳过: {e}")
        
    return ticker_map

# 启动时获取页面ID映射
PAGE_MAP = clean_and_map_database()

def get_stock_logic(ticker):
    print(f"🔍 深度扫描: {ticker}...")
    try:
        stock = yf.Ticker(ticker)
        # 强制刷新
        hist = stock.history(period="5d") 
        if hist.empty: 
            print(f"⚠️ {ticker} 无法获取K线数据")
            return None

        # 基础数据
        price = round(hist['Close'].iloc[-1], 2)
        open_p = hist['Open'].iloc[-1]
        low_p = hist['Low'].iloc[-1]
        high_p = hist['High'].iloc[-1]
        volume = hist['Volume'].iloc[-1]
        
        # --- 核心修改：智能获取流通股 (分母) ---
        share_float = 0
        
        # 1. 优先：读本地文件 (Firecrawl抓的 Finviz 数据，最准，RDW=85M)
        if ticker in FLOAT_DB:
            share_float = FLOAT_DB[ticker]
            # print(f"   📘 使用本地库数据: {share_float/1000000:.2f}M")
            
        # 2. 备用：读 YFinance 的 floatShares (比 sharesOutstanding 准)
        if not share_float:
            share_float = stock.info.get('floatShares')
            
        # 3. 兜底：读 YFinance 的 sharesOutstanding (最不准，容易把RDW算成1亿)
        if not share_float:
            share_float = stock.info.get('sharesOutstanding')

        # 计算换手率
        turnover_rate = (volume / share_float) if share_float else 0
        
        # 量比
        avg_vol = hist['Volume'].mean()
        vol_ratio = round(volume / avg_vol, 1) if avg_vol > 0 else 0
        ma_close = hist['Close'].mean()
        
        # --- 警报逻辑 ---
        price_pos = (price - low_p) / (high_p - low_p) if (high_p - low_p) != 0 else 0.5
        is_red_alert = False
        alert_msg = ""
        
        if turnover_rate > 0.5 and price_pos < 0.3:
            is_red_alert = True
            alert_msg = f"🚨 警报：天量出货 (换手 {turnover_rate:.1%})"
        elif turnover_rate > 0.6:
            is_red_alert = True
            alert_msg = f"🚨 警报：极端换手 ({turnover_rate:.1%})"

        # --- 评级逻辑 ---
        status = "L1-初选池"
        if price > ma_close: status = "L2-观察池"
        if vol_ratio > 2.0 and price > open_p: status = "L3-核心池"
        if is_red_alert: status = "L1-初选池"

        # 止损
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
    """更新 Notion 数据"""
    try:
        # 获取当前时间 (格式: 01-25 14:30)
        time_str = datetime.now().strftime("%m-%d %H:%M")
        
        # 直接查字典获取 ID，不再 query
        page_id = PAGE_MAP.get(ticker)

        tags = [{"name": data['status']}]
        if data['alert']: tags.append({"name": "🚨极端换手", "color": "red"})
        
        properties = {
            "Name": {"title": [{"text": {"content": ticker}}]},
            "Status": {"select": {"name": data['status']}},
            "Tags": {"multi_select": tags}
        }
        
        # 内容块 (增加了时间戳)
        content_block = {
            "object": "block", 
            "type": "paragraph", 
            "paragraph": {
                "rich_text": [
                    {"text": {"content": f"💰 现价: ${data['price']} | 🛡️ 止损: ${data['stop']}\n"}},
                    {"text": {"content": f"📊 换手: {data['turnover']}% | 量比: {data['vol']}x\n"}},
                    {"text": {"content": f"🕒 更新: {time_str}  ", "annotations": {"color": "gray", "italic": True}}},
                    {"text": {"content": f"{data['alert_msg']}" if data['alert'] else "", "annotations": {"color": "red"}}}
                ]
            }
        }

        if page_id:
            notion.pages.update(page_id=page_id, properties=properties)
            # 追加内容块到页面底部，这样能看到历史记录，或者单纯更新
            # 为了简单，这里我们只更新属性。如果想看正文，需要 append_children
            try: notion.blocks.children.append(block_id=page_id, children=[content_block])
            except: pass
            print(f"🔄 {ticker} 更新成功")
        else:
            new_page = notion.pages.create(
                parent={"database_id": database_id}, 
                properties=properties, 
                children=[content_block]
            )
            # 记录新ID
            PAGE_MAP[ticker] = new_page["id"]
            print(f"✨ {ticker} 创建成功")
            
    except Exception as e:
        print(f"❌ {ticker} Notion推送失败: {e}")

if __name__ == "__main__":
    print(f"🛠️ Notion Client Version: {notion_client.__version__}")
    print("🚀 开始执行每日选股任务...")
    
    for t in WATCHLIST:
        res = get_stock_logic(t)
        if res:
            update_notion(t, res)
            
    print("🏁 任务完成！")
