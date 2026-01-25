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
    except: pass

# --- 3. 核心：建立全量索引（含重复项） ---
def get_all_pages():
    """
    抓取数据库所有页面，返回字典：
    { "RDW": ["id_1", "id_2"], "AMD": ["id_3"] }
    """
    print("📋 [系统] 正在全量扫描 Notion 数据库...")
    pages_map = {}
    
    has_more = True
    start_cursor = None
    total_count = 0
    
    while has_more:
        try:
            resp = notion.databases.query(
                database_id=database_id, 
                start_cursor=start_cursor, 
                page_size=100
            )
            results = resp.get("results", [])
            total_count += len(results)
            
            for page in results:
                # 提取标题(Ticker)
                ticker = None
                for prop in page["properties"].values():
                    if prop["type"] == "title" and prop["title"]:
                        ticker = prop["title"][0]["text"]["content"].upper()
                        break
                
                if ticker:
                    if ticker not in pages_map:
                        pages_map[ticker] = []
                    pages_map[ticker].append(page["id"])
            
            has_more = resp.get("has_more")
            start_cursor = resp.get("next_cursor")
            
        except Exception as e:
            print(f"⚠️ 扫描中断: {e}")
            has_more = False
            
    print(f"   📊 共发现 {total_count} 个页面，涉及 {len(pages_map)} 只股票")
    return pages_map

# --- 4. 核心：核弹级清理 ---
def nuke_duplicates_and_old_stocks(pages_map):
    """
    1. 清理不在清单里的股票
    2. 清理重复的股票（只保留一个）
    返回：清洗后的 { "RDW": "id_1" }
    """
    print("🧨 [核弹] 启动清理程序...")
    clean_map = {} # 最终保留的 ID
    removed_count = 0

    # 遍历 Notion 里所有的股票
    all_tickers = list(pages_map.keys())
    
    for ticker in all_tickers:
        page_ids = pages_map[ticker]
        
        # 情况 A: 股票不在今日清单里 -> 全部删除
        if ticker not in WATCHLIST:
            for pid in page_ids:
                try:
                    notion.pages.update(page_id=pid, archived=True)
                    print(f"   👋 [移除旧股] {ticker}")
                    removed_count += 1
                except: pass
            continue # 处理下一个

        # 情况 B: 股票在清单里 -> 保留第1个，删除其余重复的
        # 假设列表里的第一个是最新的或者随机的，保留它即可
        keep_id = page_ids[0]
        clean_map[ticker] = keep_id # 记录到干净的地图里
        
        # 如果有重复的，删除多余的
        if len(page_ids) > 1:
            for pid in page_ids[1:]:
                try:
                    notion.pages.update(page_id=pid, archived=True)
                    print(f"   🗑️ [删除重复] {ticker} (ID: ...{pid[-4:]})")
                    removed_count += 1
                except: pass

    print(f"✨ 清理完成！共移除了 {removed_count} 个废弃/重复页面")
    return clean_map

# --- 5. 获取数据逻辑 ---
def get_stock_data(ticker):
    # ... (保持原有逻辑不变，省略部分以节省篇幅，核心是下面的逻辑) ...
    # 这里直接用你之前验证成功的逻辑
    print(f"🔍 分析: {ticker}...")
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
        
        # 简单评级
        ma = hist['Close'].mean()
        status = "L1-初选池" if price > ma else "L2-观察池"
        if turnover > 0.5: status = "L1-初选池"
        
        # 止损
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

# --- 6. 执行更新 ---
def main():
    print("🚀 任务开始...")
    
    # 第一步：获取全量数据
    full_map = get_all_pages()
    
    # 第二步：核弹清理 (只保留清单里的、唯一的卡片)
    # clean_map 里面现在只有 { "RDW": "唯一ID", "AMD": "唯一ID" }
    final_map = nuke_duplicates_and_old_stocks(full_map)
    
    # 第三步：遍历清单进行更新或创建
    for ticker in WATCHLIST:
        data = get_stock_data(ticker)
        if not data: continue
        
        # 构造内容
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

        # 核心判断：是更新还是新建？
        if ticker in final_map:
            # 如果在清洗后的地图里，说明有现成的卡片，直接更新
            page_id = final_map[ticker]
            try:
                notion.pages.update(page_id=page_id, properties=props)
                # 清空旧内容
                children = notion.blocks.children.list(block_id=page_id).get("results", [])
                for block in children: notion.blocks.delete(block_id=block["id"])
                # 写入新内容
                notion.blocks.children.append(block_id=page_id, children=text_blocks)
                print(f"🔄 更新: {ticker}")
            except Exception as e:
                print(f"❌ 更新失败 {ticker}: {e}")
        else:
            # 没找到，说明是今天新加的股票，创建它
            try:
                notion.pages.create(parent={"database_id": database_id}, properties=props, children=text_blocks)
                print(f"✨ 创建: {ticker}")
            except Exception as e:
                print(f"❌ 创建失败 {ticker}: {e}")

    print("🏁 任务全部完成！")

if __name__ == "__main__":
    main()
