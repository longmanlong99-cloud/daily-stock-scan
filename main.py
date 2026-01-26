import os
import json
import time
import requests
from datetime import datetime, timedelta

# --- 配置区 ---
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
        
        if resp.status_code >= 400:
            print(f"⚠️ Notion API Error {resp.status_code}: {resp.text}")
        return resp.json()
    except Exception as e:
        print(f"⚠️ API 请求异常: {e}")
        return {}

def get_stock_data(ticker):
    if ticker not in DAILY_CACHE: return None
    
    md = DAILY_CACHE[ticker]
    share_float = FLOAT_DB.get(ticker, 0)
    source = "🔥本地库" if ticker in FLOAT_DB else "⚠️未知"
    
    # 基础数据
    price = md['price']
    ma200 = md.get('ma200', price)
    max_pain = md.get('max_pain') # 可以是 None
    
    # 强制空值转换
    rsi = md.get('rsi')
    if rsi is None: rsi = 50
    
    atr = md.get('atr')
    if atr is None: atr = 0
    
    pcr = md.get('pcr')
    if pcr is None: pcr = 0
    
    # 计算换手率
    turnover = (md['volume'] / share_float) if share_float else 0
    
    # --- 1. 状态判定 ---
    status = "L1-初选池"
    alert = False
    alert_msg = ""
    tags = []
    
    # L2 逻辑
    if price > ma200 or md.get('vol_ratio', 0) > 2.0:
        status = "L2-观察池"
        
    # --- L3 逻辑 (痛点 + 换手率深度博弈) ---
    pain_deviation = 0
    is_pain_alert = False # 标记是否触发了痛点警报
    pain_status_desc = "" 
    
    if max_pain:
        pain_deviation = (price - max_pain) / max_pain
        abs_dev = abs(pain_deviation)
        
        # [逻辑A] 动态阈值 (震荡期严，趋势期宽)
        threshold = 0.2 if (45 <= rsi <= 55) else 0.4
        
        # [逻辑B] 换手率豁免 (Key Point!)
        # >15% (0.15) 视为活跃，忽略痛点引力
        is_active_turnover = (turnover > 0.15)
        
        # [逻辑C] 痛点失效判定
        # 如果偏差 > 80% (0.8) 或者 换手活跃，则痛点逻辑失效（不报警偏离）
        is_pain_failed = (abs_dev > 0.8) or is_active_turnover
        
        if not is_pain_failed:
            if abs_dev > threshold:
                status = "L3-核心池"
                alert = True
                is_pain_alert = True
                alert_msg = f"⚡ 偏离痛点 {abs_dev:.0%} (Pain:${max_pain})"
                pain_status_desc = "偏离需回归"
        else:
            # 虽然痛点失效，但我们要区分是 "好事(突破)" 还是 "坏事(太妖)"
            if is_active_turnover:
                pain_status_desc = "放量博弈" # 中性词，具体看换手率大小
            else:
                pain_status_desc = "趋势脱离引力"

    # --- 换手率独立警报 (分层防御) ---
    # 💀 死亡换手层 (>40%)：极度危险，可能是出货
    if turnover > 0.4:
        alert = True
        alert_msg = f"💀 死亡换手 {turnover:.1%} (警惕见顶/出货)"
        # 如果此时 RSI 还很高，几乎必死
        if rsi > 75: alert_msg += " + RSI过热"
        
    # 🚨 极端换手层 (>20%)：非常活跃
    elif turnover > 0.2:
        alert = True
        # 如果没被上面的死亡换手覆盖，才显示这个
        if "死亡换手" not in alert_msg:
            alert_msg = f"🚨 极端换手 {turnover:.1%}"

    # --- 2. 智能文案生成 ---
    # A. PCR
    pcr_desc = "情绪中性"
    if pcr > 0:
        if pcr < 0.6: 
            pcr_desc = "散户狂热"
            tags.append({"name": "🐂散户狂热", "color": "yellow"})
        elif pcr > 1.0: 
            pcr_desc = "极度悲观"
            tags.append({"name": "🐻极度悲观", "color": "gray"})
    
    # B. RSI
    rsi_desc = "正常"
    if rsi > 75: 
        rsi_desc = "严重超买"
        tags.append({"name": "🔥RSI过热", "color": "orange"})
    elif rsi < 30: 
        rsi_desc = "超卖区"
        tags.append({"name": "💎RSI超卖", "color": "green"})

    # C. 点评 (针对 50% 换手率优化)
    commentary = "👨‍⚕️ 点评: "
    if price > ma200: commentary += "长期趋势向上。"
    else: commentary += "长期趋势走弱。"
    
    # 风险因子
    if is_pain_alert:
        commentary += f" 但价格严重偏离痛点（{abs(pain_deviation):.0%}），且换手不足，谨防回落。"
    elif turnover > 0.4:
        # 针对 40%+ 换手的特殊点评
        commentary += f" ⚠️ 注意：出现 {turnover:.1%} 的死亡换手率！这通常是多空决战或主力出货信号，若股价滞涨务必离场。"
    elif max_pain and pain_status_desc == "放量博弈":
        commentary += f" 配合 {turnover:.1%} 的高换手，动能极强，已突破痛点压制（关注资金接力情况）。"
    elif max_pain and pain_status_desc == "趋势脱离引力":
        commentary += " 动能已无视期权痛点，顺势而为。"
    elif status == "L2-观察池":
        commentary += " 量价配合健康，可沿动态止损持有。"
    else:
        commentary += " 震荡观察。"

    # --- 3. 标签与止损 ---
    tags.insert(0, {"name": status})
    if alert: tags.append({"name": "🚨警报", "color": "red"})
    
    stop_loss = round(price - 3 * atr, 2) if atr > 0 else round(ma200 * 0.95, 2)

    return {
        "price": price, "status": status, 
        "stop": stop_loss,
        "turnover": round(turnover*100, 2), 
        "vol": md.get('vol_ratio', 0),
        "source": source,
        "alert": alert, "alert_msg": alert_msg,
        "max_pain": max_pain,
        "pain_deviation_abs": abs(pain_deviation),
        "pain_status_desc": pain_status_desc, 
        "rsi": rsi, "rsi_desc": rsi_desc,
        "pcr": pcr, "pcr_desc": pcr_desc,
        "tags": tags,
        "commentary": commentary
    }

def sync_notion_data():
    print("🚀 启动 Notion 同步 (死亡换手预警版)...")
    
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
        
        rich_text_list = []

        # Line 1
        line1 = f"💰 现价: ${data['price']} | 🛑 动态止损: ${data['stop']} (3ATR)\n"
        rich_text_list.append({"type": "text", "text": {"content": line1}})

        # Line 2
        pcr_info = f"🐂 PCR: {data['pcr']} ({data['pcr_desc']})" if data['pcr'] > 0 else "PCR: --"
        rsi_info = f"📊 RSI: {data['rsi']} ({data['rsi_desc']})"
        line2 = f"{rsi_info} | {pcr_info}\n"
        rich_text_list.append({"type": "text", "text": {"content": line2}})

        # Line 3 (精细化显示)
        pain_str = "痛点: --"
        if data['max_pain']:
            pain_str = f"🎯 痛点: ${data['max_pain']}"
            if data['pain_status_desc'] == "放量博弈":
                 pain_str += " (🔥天量)"
            elif data['pain_status_desc'] == "趋势脱离引力":
                 pain_str += " (🚀失效)"
        
        vol_info = f"📈 换手: {data['turnover']}%"
        line3 = f"{pain_str} | {vol_info}\n"
        rich_text_list.append({"type": "text", "text": {"content": line3}})

        # 点评
        if data['commentary']:
            rich_text_list.append({
                "type": "text", 
                "text": {"content": "\n" + data['commentary'] + "\n"},
                "annotations": {"color": "gray", "italic": True}
            })

        # 底部
        rich_text_list.append({
            "type": "text", 
            "text": {"content": f"ℹ️ 源: {data['source']} | 🕒 {cst_time}\n"},
            "annotations": {"color": "gray"}
        })

        # 警报 (逻辑升级)
        if data['alert'] and data['alert_msg']:
            # 如果是死亡换手，用紫色或粗体强调
            color = "red"
            if "死亡换手" in data['alert_msg']:
                color = "purple" # Notion 支持 purple，表示更高级别的警报
            
            rich_text_list.append({
                "type": "text", 
                "text": {"content": data['alert_msg']},
                "annotations": {"color": color, "bold": True}
            })
        
        # 补充提示
        if not data['alert'] and data['pain_status_desc'] == "放量博弈":
             rich_text_list.append({
                "type": "text", 
                "text": {"content": f"🔥 换手活跃 ({data['turnover']}%)，暂时无视痛点引力"},
                "annotations": {"color": "blue"}
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
            print(f"   ✅ 更新: {ticker}")
        else:
            new_page = {"parent": {"database_id": DATABASE_ID}, "properties": properties, "children": children_blocks}
            notion_api("POST", "/pages", new_page)
            print(f"   ✨ 新建: {ticker}")

    # 3. 清理
    print("🧹 [3/3] 清理废弃数据...")
    for ticker, page_id in existing_pages.items():
        if ticker not in processed_tickers:
            notion_api("PATCH", f"/pages/{page_id}", {"archived": True})

    print("🏁 完成！")

if __name__ == "__main__":
    sync_notion_data()
