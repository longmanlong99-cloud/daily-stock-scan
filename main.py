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
        # 确保清单干净、大写、去重
        WATCHLIST = list(set([l.strip().upper() for l in f if l.strip()]))

notion = Client(auth=os.environ.get("NOTION_TOKEN"))
database_id = os.environ.get("NOTION_DATABASE_ID")

# --- 2. 加载本地数据 ---
FLOAT_DB = {}
if os.path.exists("float_data.json"):
    try:
        with open("float_data.json", "r") as f:
            FLOAT_DB = json.load(f)
        print(f"📘 已加载本地数据库: {len(FLOAT_DB)} 条记录")
    except: pass

# --- 3. 核心：建立全量索引 ---
def get_all_pages():
    print("📋 [系统] 正在全量扫描 Notion 数据库...")
    pages_map = {}
    has_more = True
    start_cursor = None
    
    while has_more:
        try:
            # 只要第一步改了YML，这里就不会报错了
            resp = notion.databases.query(
                database_id=database_id, 
                start_cursor=start_cursor, 
                page_size=100
            )
            results = resp.get("results", [])
            
            for page in results:
                ticker = None
                for prop in page["properties"].values():
                    if prop["type"] == "title" and prop["title"]:
                        ticker = prop["title"][0]["text"]["content"].upper()
                        break
                
                if ticker:
                    if ticker not in pages_map: pages_map[ticker] = []
                    pages_map[ticker].append(page["id"])
            
            has_more = resp.get("has_more")
            start_cursor = resp.get("next_cursor")
            
        except Exception as e:
            print(f"⚠️ 扫描报错 (如果看到这个，请务必检查YML文件): {e}")
            has_more = False 
            
    return pages_map

# --- 4. 核心：核弹清理 (解决列表越来越长的问题) ---
def nuke_duplicates(pages_map):
    print("🧨 [核弹] 启动清理程序...")
    clean_map = {} 
    
    # 遍历所有存在的股票
    for ticker, page_ids in pages_map.items():
        # 情况A：股票不在今天的 stocks.txt 里 -> 全部删除
        if ticker not in WATCHLIST:
            for pid in page_ids:
                try: notion.pages.update(page_id=pid, archived=True)
                except: pass
            print(f"   👋 移除不在清单的旧股: {ticker}")
            continue

        # 情况B：在清单里 -> 保留最新的1个，删掉其他的
        keep_id = page_ids[0]
        clean_map[ticker] = keep_id 
        
        # 删除多余的
        if len(page_ids) > 1:
            for pid in page_ids[1:]:
                try: notion.pages.update(page_id=pid, archived=True)
                except: pass
            print(f"   🗑️ 删除重复项: {ticker} ({len(page_ids)-1}个)")

    return clean_map

# --- 5. 获取数据 ---
def get_stock_data(ticker):
    print(f"🔍 分析: {ticker}...", end="")
    try:
        stock = yf.Ticker(ticker)
        hist = stock.history(period="5d")
        if hist.empty: 
            print(" ⚠️ 无数据")
            return None
        
        price = round(hist['Close'].iloc[-1], 2)
        volume = hist['Volume'].iloc[-1]
        
        # --- 找流通股 ---
        share_float = 0
        source = "❓"
        
        # 1. 优先读本地文件 (RDW会走这里)
        if ticker in FLOAT_DB: 
            share_float = FLOAT_DB[ticker]
            source = "🔥本地库"
        # 2. 备用
        elif stock.info.get('floatShares'):
            share_float = stock.info.get('floatShares')
            source = "⚠️Yahoo"
        # 3. 兜底
        else:
            share_float = stock.info.get('sharesOutstanding')
            source = "⚠️总股本"
            
        # --- ✅ 这里把显示加回来了 ---
        print(f" 📊 股本: {share_float/1000000:.2f}M ({source})")
        # -------------------------------

        turnover = (volume / share_float) if share_float else 0
        
        # 评级逻辑
        ma = hist['Close'].mean()
        status = "L1-初选池" if price > ma else "L2-观察池"
        if turnover > 0.5: status = "L1-初选池"
        
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
    except Exception as e: 
        print(f" Error: {e}")
        return None

# --- 6. 主程序 ---
def main():
    print("🚀 任务开始...")
    
    # 1. 获取全量索引 (只要第一步改了YML，这步就能跑通)
    full_map = get_all_pages()
    
    # 2. 清理重复项
    final_map = nuke_duplicates(full_map)
    
    # 3. 更新/新建
    for ticker in WATCHLIST:
        data = get_stock_data(ticker)
        if not data: continue
        
        cst_time = (datetime.utcnow() - timedelta(hours=6)).strftime("%m-%d %H:%M CST")
        tags = [{"name": data['status']}]
        if data['alert']: tags.append({"name": "🚨极端换手", "color": "red"})
        
        props = {
            "Name": {"title": [{"text": {"content": ticker}}]},
            "Status": {"select": {"name": data['status']}},
            "Tags": {"multi_select": tags}
        }
        
        text_blocks = [
            {"object": "block", "type": "paragraph", "paragraph": {"rich_text": [
                {"type": "text", "text": {"content": f"💰 现价: ${data['price']} | 🛡️ 止损: ${data['stop']}\n"}},
                {"type": "text", "text": {"content": f"📊 换手: {data['turnover']}% | 量比: {data['vol']}x\n"}},
                {"type": "text", "text": {"content": f"ℹ️ 源: {data['source']} | 🕒 {cst_time}\n"}, "annotations": {"color": "gray", "italic": True}},
                {"type": "text", "text": {"content": f"{data['alert_msg']}" if data['alert'] else ""}, "annotations": {"color": "red"}}
            ]}}
        ]

        if ticker in final_map:
            # 更新现有 (先清空内容再写)
            page_id = final_map[ticker]
            try:
                notion.pages.update(page_id=page_id, properties=props)
                # 清空旧文字
                children = notion.blocks.children.list(block_id=page_id).get("results", [])
                for block in children: notion.blocks.delete(block_id=block["id"])
                # 写入新文字
                notion.blocks.children.append(block_id=page_id, children=text_blocks)
                print(f"   🔄 更新成功")
            except: pass
        else:
            # 新建
            try:
                notion.pages.create(parent={"database_id": database_id}, properties=props, children=text_blocks)
                print(f"   ✨ 创建成功")
            except: pass

    print("🏁 任务全部完成！")

if __name__ == "__main__":
    main()
