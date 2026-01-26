import os
import json
import time
import requests
from datetime import datetime, timedelta

# ==========================================
# 1. 策略配置 (Strategy Config)
# ==========================================
CONFIG = {
    "LARGE_CAP_THRESHOLD": 10_000_000_000, 
    
    # 换手率熔断阈值 (Turnover Limits)
    "LARGE_CAP_TURNOVER_LIMIT": 0.08,      # 大盘股 > 8% (避免误杀活跃股)
    "SMALL_CAP_TURNOVER_LIMIT": 0.35,      # 小盘股 > 35% (专杀 RDW 59%)
    
    # 痛点偏离阈值 (Pain Deviation Limits) - 【本次核心修改】
    "PAIN_LIMIT_LARGE": 0.15,              # 大盘股：偏离 15% 即为核心机会
    "PAIN_LIMIT_SMALL": 0.30,              # 小盘股：偏离 30% 才算核心机会
    
    "RSI_MAX_LIMIT": 75
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
    ma60 = md.get('ma60', ma200)
    ma20 = md.get('ma20', price)
    max_pain = md.get('max_pain')
    
    rsi = md.get('rsi') if md.get('rsi') is not None else 50
    atr = md.get('atr') if md.get('atr') is not None else 0
    pcr = md.get('pcr') if md.get('pcr') is not None else 0
    
    turnover = (md['volume'] / share_float) if share_float else 0
    market_cap = price * share_float if share_float else 0

    # --- 2. 状态判定 (智能分级逻辑) ---

    status = "L1-初选池"  
    tags = []
    alert = False
    alert_msg = ""
    commentary_parts = []
    style_emoji = "⚪" 

    # --- A. 确定该股票的特定阈值 (动态调整) ---
    is_large_cap = market_cap > CONFIG["LARGE_CAP_THRESHOLD"]
    
    # 1. 换手率阈值
    turnover_limit = CONFIG["LARGE_CAP_TURNOVER_LIMIT"] if is_large_cap else CONFIG["SMALL_CAP_TURNOVER_LIMIT"]
    
    # 2. 痛点偏离阈值 (本次新增：大盘15%，小盘30%)
    pain_limit = CONFIG["PAIN_LIMIT_LARGE"] if is_large_cap else CONFIG["PAIN_LIMIT_SMALL"]

    # --- B. 第一层：熔断检测 ---
    is_high_risk = False
    if turnover > turnover_limit:
        is_high_risk = True
        risk_type = "大盘股滞涨" if is_large_cap else "小盘股死亡换手"
        alert_msg = f"☠️ {risk_type} ({turnover:.1%})"

    # --- C. 第二层：逻辑分流 ---
    
    # 计算痛点偏离度
    pain_deviation = 0
    if max_pain:
        pain_deviation = (price - max_pain) / max_pain

    if is_high_risk:
        # [Case 1] 触发熔断
        status = "L3-高危/异常"
        style_emoji = "🚨"
        alert = True
        tags.append({"name": "⚡高危", "color": "red"})
        commentary_parts.append(f"触发量能熔断！{alert_msg}")

    elif max_pain and abs(pain_deviation) > pain_limit:
        # [Case 2] 核心机会 (使用了动态的 pain_limit)
        status = "L3-核心池"
        style_emoji = "💎"
        tags.append({"name": "🧲偏离痛点", "color": "purple"})
        # 在点评里明确显示当前使用的阈值，方便您核对
        commentary_parts.append(f"严重偏离痛点{abs(pain_deviation):.0%} (阈值:{pain_limit:.0%})，关注回归")

    elif price > ma60 and rsi < CONFIG["RSI_MAX_LIMIT"]:
        # [Case 3] 观察池
        status = "L2-观察池"
        style_emoji = "🟢"
        trend_desc = "趋势向上"
        if price > ma20: trend_desc += "且短期强势"
        commentary_parts.append(trend_desc)

    else:
        # [Case 4] 初选池
        status = "L1-初选池"
        style_emoji = "💤"
        if price <= ma60:
            commentary_parts.append("趋势震荡或跌破均线")
        elif rsi >= CONFIG["RSI_MAX_LIMIT"]:
            commentary_parts.append(f"RSI过热({rsi})")

    # --- 3. 标签与文案完善 ---
    
    pcr_desc = "中性"
    if pcr > 0:
        if pcr < 0.6: 
            pcr_desc = "狂热"
            tags.append({"name": "🐂散户狂热", "color": "yellow"})
        elif pcr > 1.0: 
            pcr_desc = "悲观"
            tags.append({"name": "🐻极度悲观", "color": "gray"})
    
    rsi_desc = "正常"
    if rsi > 75: 
        rsi_desc = "超买"
        tags.append({"name": "🔥RSI过热", "color": "orange"})
    elif rsi < 30: 
        rsi_desc = "超卖"
        tags.append({"name": "💎RSI超卖", "color": "green"})

    commentary = "👨‍⚕️ 点评: " + "，".join(commentary_parts)
    
    tags.insert(0, {"name": status})
    if alert and status != "L3-高危/异常":
        tags.append({"name": "🚨警报", "color": "red"})
    
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
        "commentary": commentary,
        "style": style_emoji
    }

def sync_notion_data():
    print("🚀 启动 Notion 同步 (分级痛点版)...")
    
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

        # 打印日志
        print(f"   {data['style']} {ticker:<6} -> {data['status']:<10} | 换手:{data['turnover']}%")

        if ticker in existing_pages:
            page_id = existing_pages[ticker]
            notion_api("PATCH", f"/pages/{page_id}", {"properties": properties})
            blocks = notion_api("GET", f"/blocks/{page_id}/children")
            for block in blocks.get("results", []): notion_api("DELETE", f"/blocks/{block['id']}")
            notion_api("PATCH", f"/blocks/{page_id}/children", {"children": children_blocks})
        else:
            new_page = {"parent": {"database_id": DATABASE_ID}, "properties": properties, "children": children_blocks}
            notion_api("POST", "/pages", new_page)

    print("🧹 [3/3] 清理废弃数据...")
    for ticker, page_id in existing_pages.items():
        if ticker not in processed_tickers:
            notion_api("PATCH", f"/pages/{page_id}", {"archived": True})

    print("🏁 完成！")

if __name__ == "__main__":
    sync_notion_data()
