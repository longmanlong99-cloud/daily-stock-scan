import os
import json
import time
import requests
from datetime import datetime, timedelta

# --- 策略配置 (Logic Config) ---
CONFIG = {
    "LARGE_CAP_THRESHOLD": 10_000_000_000, 
    "LARGE_CAP_TURNOVER_LIMIT": 0.05,      
    "SMALL_CAP_TURNOVER_LIMIT": 0.20,      # RDW 杀手
    "RSI_MAX_LIMIT": 75,
    "PAIN_DEVIATION_LIMIT": 0.20           # 痛点偏离 20% 进核心池
}

# --- 基础配置 ---
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

# --- 加载数据 ---
FLOAT_DB = {}
if os.path.exists("float_data.json"):
    try:
        with open("float_data.json", "r") as f: FLOAT_DB = json.load(f)
    except: pass

DAILY_CACHE = {}
if os.path.exists("daily_cache.json"):
    try:
        with open("daily_cache.json", "r") as f: DAILY_CACHE = json.load(f)
        print(f"📗 已加载深度行情缓存: {len(DAILY_CACHE)} 条")
    except: pass

# --- 核心功能 ---

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
    if ticker not in DAILY_CACHE: return None
    
    md = DAILY_CACHE[ticker]
    share_float = FLOAT_DB.get(ticker, 0)
    source = "🔥本地库" if ticker in FLOAT_DB else "⚠️未知"
    
    # 1. 提取基础数据
    price = md['price']
    ma200 = md.get('ma200', price)
    ma60 = md.get('ma60', ma200)  # 中期趋势
    ma20 = md.get('ma20', price)  # 短期趋势
    max_pain = md.get('max_pain')
    
    rsi = md.get('rsi') if md.get('rsi') is not None else 50
    atr = md.get('atr') if md.get('atr') is not None else 0
    pcr = md.get('pcr') if md.get('pcr') is not None else 0
    
    turnover = (md['volume'] / share_float) if share_float else 0
    market_cap = price * share_float if share_float else 0

    # --- 2. 状态判定 (全逻辑恢复) ---
    
    # [层级 0] 默认状态：L1-初选池
    # 只要不符合后面任何条件，就会留在这里 (相当于原来的兜底)
    status = "L1-初选池" 
    tags = []
    alert = False
    alert_msg = ""
    
    commentary_parts = []

    # [层级 1] 趋势筛选 -> L2-观察池
    # 要求：站上 MA60 且 RSI 健康
    if price > ma60 and rsi < CONFIG["RSI_MAX_LIMIT"]:
        status = "L2-观察池"
        commentary_parts.append("趋势向上(>MA60)")
    else:
        # 如果还在 L1，说明趋势不好
        commentary_parts.append("趋势震荡或走弱")

    # [层级 2] 核心机会 -> L3-核心池 (恢复逻辑)
    # 逻辑：价格偏离 Max Pain 太多，有回归引力
    pain_deviation = 0
    if max_pain:
        pain_deviation = (price - max_pain) / max_pain
        if abs(pain_deviation) > CONFIG["PAIN_DEVIATION_LIMIT"]:
            status = "L3-核心池"
            alert = True # 核心池也是一种特别提醒
            tags.append({"name": "🧲偏离痛点", "color": "purple"})
            commentary_parts.append(f"严重偏离痛点{abs(pain_deviation):.0%}，关注回归")

    # [层级 3] 死亡熔断 -> L3-高危/异常 (最高优先级，覆盖一切)
    is_high_risk = False
    
    # 大盘股熔断
    if market_cap > CONFIG["LARGE_CAP_THRESHOLD"]:
        if turnover > CONFIG["LARGE_CAP_TURNOVER_LIMIT"]:
            is_high_risk = True
            alert_msg = f"🚨 大盘股滞涨风险 ({turnover:.1%})"
    # 小盘股熔断 (RDW)
    else:
        if turnover > CONFIG["SMALL_CAP_TURNOVER_LIMIT"]:
            is_high_risk = True
            alert_msg = f"☠️ 小盘股死亡换手 ({turnover:.1%})"

    if is_high_risk:
        status = "L3-高危/异常"
        alert = True
        tags.append({"name": "⚡高危", "color": "red"})
        # 清空之前的废话，直接警告
        commentary_parts = [f"⚠️ 触发量能熔断！{alert_msg}，必须规避！"]

    # --- 3. 标签与文案完善 ---
    
    # PCR 标签
    pcr_desc = "中性"
    if pcr > 0:
        if pcr < 0.6: 
            pcr_desc = "狂热"
            tags.append({"name": "🐂散户狂热", "color": "yellow"})
        elif pcr > 1.0: 
            pcr_desc = "悲观"
            tags.append({"name": "🐻极度悲观", "color": "gray"})
    
    # RSI 标签
    rsi_desc = "正常"
    if rsi > 75: 
        rsi_desc = "超买"
        tags.append({"name": "🔥RSI过热", "color": "orange"})
    elif rsi < 30: 
        rsi_desc = "超卖"
        tags.append({"name": "💎RSI超卖", "color": "green"})

    # 最终点评拼接
    commentary = "👨‍⚕️ 点评: " + "，".join(commentary_parts)
    
    # 状态标签置顶
    tags.insert(0, {"name": status})
    if alert and status != "L3-高危/异常" and status != "L3-核心池":
        tags.append({"name": "🚨警报", "color": "red"})
    
    # 动态止损
    stop_loss = round(price - 3 * atr, 2) if atr > 0 else round(ma200 * 0.95, 2)

    return {
        "price": price, 
        "status": status, 
        "stop": stop_loss,
        "turnover": round(turnover*100, 2), 
        "vol": md.get('vol_ratio', 0),
        "source": source,
        "alert": alert, 
        "alert_msg": alert_msg,
        "max_pain": max_pain,
        "rsi": rsi, 
        "rsi_desc": rsi_desc,
        "pcr": pcr, 
        "pcr_desc": pcr_desc,
        "tags": tags,
        "commentary": commentary
    }

def sync_notion_data():
    print("🚀 启动 Notion 同步 (全逻辑恢复版)...")
    
    # 1. 扫描
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
                ticker = page["properties"]["Name"]["title"][0]["text"]["content"].upper()
                if ticker in existing_pages: notion_api("PATCH", f"/pages/{page['id']}", {"archived": True})
                else: existing_pages[ticker] = page["id"]
            except: pass
        has_more = data.get("has_more", False)
        start_cursor = data.get("next_cursor")

    # 2. 更新
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
            "Tags": {"multi_select": data['tags']}
        }
        
        # --- Rich Text 拼接 ---
        rich_text_list = []

        line1 = f"💰 现价: ${data['price']} | 🛑 动态止损: ${data['stop']} (3ATR)\n"
        rich_text_list.append({"type": "text", "text": {"content": line1}})

        pcr_info = f"🐂 PCR: {data['pcr']} ({data['pcr_desc']})" if data['pcr'] > 0 else "PCR: --"
        rsi_info = f"📊 RSI: {data['rsi']} ({data['rsi_desc']})"
        line2 = f"{rsi_info} | {pcr_info}\n"
        rich_text_list.append({"type": "text", "text": {"content": line2}})

        pain_info = f"🎯 痛点: ${data['max_pain']} (庄家目标)" if data['max_pain'] else "痛点: --"
        vol_info = f"📈 换手: {data['turnover']}%"
        line3 = f"{pain_info} | {vol_info}\n"
        rich_text_list.append({"type": "text", "text": {"content": line3}})

        if data['commentary']:
            rich_text_list.append({
                "type": "text", 
                "text": {"content": "\n" + data['commentary'] + "\n"},
                "annotations": {"color": "gray", "italic": True}
            })

        rich_text_list.append({
            "type": "text", 
            "text": {"content": f"ℹ️ 源: {data['source']} | 🕒 {cst_time}\n"},
            "annotations": {"color": "gray"}
        })

        if data['alert'] and data['alert_msg']:
            rich_text_list.append({
                "type": "text", 
                "text": {"content": data['alert_msg']},
                "annotations": {"color": "red", "bold": True}
            })

        children_blocks = [
            {"object": "block", "type": "paragraph", "paragraph": {"rich_text": rich_text_list}}
        ]

        if ticker in existing_pages:
            page_id = existing_pages[ticker]
            notion_api("PATCH", f"/pages/{page_id}", {"properties": properties})
            blocks = notion_api("GET", f"/blocks/{page_id}/children")
            for block in blocks.get("results", []): notion_api("DELETE", f"/blocks/{block['id']}")
            notion_api("PATCH", f"/blocks/{page_id}/children", {"children": children_blocks})
            print(f"   ✅ 更新: {ticker} [{data['status']}]")
        else:
            new_page = {"parent": {"database_id": DATABASE_ID}, "properties": properties, "children": children_blocks}
            notion_api("POST", "/pages", new_page)
            print(f"   ✨ 新建: {ticker} [{data['status']}]")

    # 3. 清理
    print("🧹 [3/3] 清理废弃数据...")
    for ticker, page_id in existing_pages.items():
        if ticker not in processed_tickers:
            notion_api("PATCH", f"/pages/{page_id}", {"archived": True})

    print("🏁 完成！")

if __name__ == "__main__":
    sync_notion_data()
