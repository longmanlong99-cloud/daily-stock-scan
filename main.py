import os
import json
import time
import requests
from datetime import datetime, timedelta

# --- 策略配置 (新增) ---
# 这些是控制 L2/L3 归类的核心参数
CONFIG = {
    "LARGE_CAP_THRESHOLD": 10_000_000_000, # 100亿是大盘股
    "LARGE_CAP_TURNOVER_LIMIT": 0.05,      # 大盘股 > 5% 换手 = 危险
    "SMALL_CAP_TURNOVER_LIMIT": 0.20,      # 小盘股 > 20% 换手 = 危险 (RDW 59% 会在这里被杀)
    "RSI_MAX_LIMIT": 75                    # RSI 过热阈值
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
    """保持原样的 Notion API 通信函数"""
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
    """
    🔥 核心修改区域：植入 '逻辑层A' 和 '逻辑层B'
    保持了原有的数据结构返回，确保 Notion 推送不报错
    """
    if ticker not in DAILY_CACHE: return None
    
    md = DAILY_CACHE[ticker]
    share_float = FLOAT_DB.get(ticker, 0)
    source = "🔥本地库" if ticker in FLOAT_DB else "⚠️未知"
    
    # 1. 提取基础数据 (兼容 fetch_data.py 新增的字段)
    price = md['price']
    
    # 尝试获取 MA60/MA20，如果旧缓存里没有，暂时回退到 MA200 或现价
    ma200 = md.get('ma200', price)
    ma60 = md.get('ma60', ma200)  # 新增: 中期生命线
    ma20 = md.get('ma20', price)  # 新增: 短期趋势线
    
    max_pain = md.get('max_pain')
    
    # 强制空值转换
    rsi = md.get('rsi')
    if rsi is None: rsi = 50
    
    atr = md.get('atr')
    if atr is None: atr = 0
    
    pcr = md.get('pcr')
    if pcr is None: pcr = 0
    
    # 计算换手率
    turnover = (md['volume'] / share_float) if share_float else 0
    market_cap = price * share_float if share_float else 0

    # --- 2. 状态判定逻辑 (Logic A + B) ---
    
    status = "L3-弱势/观望" # 默认初始状态
    tags = []
    alert = False
    alert_msg = ""
    
    # === 🚨 逻辑层 A: 死亡换手熔断 (Circuit Breaker) ===
    is_high_risk = False
    
    if market_cap > CONFIG["LARGE_CAP_THRESHOLD"]:
        # 大盘股逻辑
        if turnover > CONFIG["LARGE_CAP_TURNOVER_LIMIT"]:
            is_high_risk = True
            alert_msg = f"🚨 大盘股滞涨风险 ({turnover:.1%})"
    else:
        # 小盘股逻辑 (RDW 会在这里被捕获)
        if turnover > CONFIG["SMALL_CAP_TURNOVER_LIMIT"]:
            is_high_risk = True
            alert_msg = f"☠️ 小盘股死亡换手 ({turnover:.1%})"

    # === 🔍 逻辑层 B: 趋势筛选 (Trend Selection) ===
    if is_high_risk:
        status = "L3-高危/异常"
        alert = True
        tags.append({"name": "⚡高危", "color": "red"})
    else:
        # 只有没触发熔断，才看趋势
        # 标准：站上 MA60 (中期向上) 且 RSI 没炸
        if price > ma60 and rsi < CONFIG["RSI_MAX_LIMIT"]:
            status = "L2-观察池"
        else:
            status = "L3-弱势/观望"

    # --- 3. 智能文案生成 (保持原有风格) ---
    
    # PCR 标签
    pcr_desc = "情绪中性"
    if pcr > 0:
        if pcr < 0.6: 
            pcr_desc = "散户狂热"
            tags.append({"name": "🐂散户狂热", "color": "yellow"})
        elif pcr > 1.0: 
            pcr_desc = "极度悲观"
            tags.append({"name": "🐻极度悲观", "color": "gray"})
    
    # RSI 标签
    rsi_desc = "正常"
    if rsi > 75: 
        rsi_desc = "严重超买"
        tags.append({"name": "🔥RSI过热", "color": "orange"})
    elif rsi < 30: 
        rsi_desc = "超卖区"
        tags.append({"name": "💎RSI超卖", "color": "green"})

    # 生成医生点评
    commentary = "👨‍⚕️ 点评: "
    
    if is_high_risk:
        commentary += f"⚠️ 触发量能熔断！{alert_msg}，建议规避。"
    else:
        # 趋势点评
        if price > ma60:
            commentary += "中期趋势(MA60)向上，"
            if price > ma20: commentary += "短期走势强劲。"
            else: commentary += "但短期有回踩需求。"
        else:
            commentary += "中期趋势(MA60)走坏，建议观望。"
        
        # 风险提示
        risk_factors = []
        if rsi > 75: risk_factors.append("RSI过热")
        if pcr > 0 and pcr < 0.6: risk_factors.append("情绪狂热")
        
        # 痛点偏离逻辑 (保留原有的痛点监测)
        if max_pain:
            pain_deviation = (price - max_pain) / max_pain
            if abs(pain_deviation) > 0.2:
                risk_factors.append(f"偏离痛点{abs(pain_deviation):.0%}")
                # 如果偏离太大，也给个警报 Tag
                tags.append({"name": "🧲偏离痛点", "color": "purple"})

        if risk_factors:
            commentary += f" 注意: {'、'.join(risk_factors)}。"
        elif status == "L2-观察池":
            commentary += " 量价配合健康，可关注低吸机会。"

    # --- 4. 组装返回数据 ---
    tags.insert(0, {"name": status}) # 把状态放在第一个标签
    if alert: tags.append({"name": "🚨警报", "color": "red"})
    
    # 动态止损计算 (保持不变)
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
    """
    保持原样不动，只负责搬运 get_stock_data 产生的数据
    """
    print("🚀 启动 Notion 同步 (逻辑升级版)...")
    
    # 1. 扫描 (保留原逻辑)
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

    # 2. 更新 (保留原排版逻辑)
    print(f"🔄 [2/3] 更新 {len(WATCHLIST)} 只股票...")
    processed_tickers = []
    
    for ticker in WATCHLIST:
        data = get_stock_data(ticker)
        if not data: continue
        processed_tickers.append(ticker)
        
        cst_time = (datetime.utcnow() - timedelta(hours=6)).strftime("%m-%d %H:%M CST")
        
        # 属性包
        properties = {
            "Name": {"title": [{"text": {"content": ticker}}]},
            "Status": {"select": {"name": data['status']}},
            "Tags": {"multi_select": data['tags']}
        }
        
        # --- Rich Text 拼接 (完全保留您的设计) ---
        rich_text_list = []

        # Line 1: 价格 + 止损
        line1 = f"💰 现价: ${data['price']} | 🛑 动态止损: ${data['stop']} (3ATR)\n"
        rich_text_list.append({"type": "text", "text": {"content": line1}})

        # Line 2: RSI + PCR
        pcr_info = f"🐂 PCR: {data['pcr']} ({data['pcr_desc']})" if data['pcr'] > 0 else "PCR: --"
        rsi_info = f"📊 RSI: {data['rsi']} ({data['rsi_desc']})"
        line2 = f"{rsi_info} | {pcr_info}\n"
        rich_text_list.append({"type": "text", "text": {"content": line2}})

        # Line 3: 痛点 + 换手
        pain_info = f"🎯 痛点: ${data['max_pain']} (庄家目标)" if data['max_pain'] else "痛点: --"
        vol_info = f"📈 换手: {data['turnover']}%"
        line3 = f"{pain_info} | {vol_info}\n"
        rich_text_list.append({"type": "text", "text": {"content": line3}})

        # Line 4: 医生点评 (内容已在 get_stock_data 里动态生成)
        if data['commentary']:
            rich_text_list.append({
                "type": "text", 
                "text": {"content": "\n" + data['commentary'] + "\n"},
                "annotations": {"color": "gray", "italic": True}
            })

        # Line 5: 底部源信息
        rich_text_list.append({
            "type": "text", 
            "text": {"content": f"ℹ️ 源: {data['source']} | 🕒 {cst_time}\n"},
            "annotations": {"color": "gray"}
        })

        # Line 6: 红色警报 (如果有)
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
            # 清空旧 Block 重新写入 (保持原逻辑)
            blocks = notion_api("GET", f"/blocks/{page_id}/children")
            for block in blocks.get("results", []): notion_api("DELETE", f"/blocks/{block['id']}")
            notion_api("PATCH", f"/blocks/{page_id}/children", {"children": children_blocks})
            print(f"   ✅ 更新: {ticker} [{data['status']}]")
        else:
            new_page = {"parent": {"database_id": DATABASE_ID}, "properties": properties, "children": children_blocks}
            notion_api("POST", "/pages", new_page)
            print(f"   ✨ 新建: {ticker} [{data['status']}]")

    # 3. 清理 (保留原逻辑)
    print("🧹 [3/3] 清理废弃数据...")
    for ticker, page_id in existing_pages.items():
        if ticker not in processed_tickers:
            notion_api("PATCH", f"/pages/{page_id}", {"archived": True})

    print("🏁 完成！")

if __name__ == "__main__":
    sync_notion_data()
