import os
import json
import time
import re
import requests

# --- 配置区 ---
STOCKS_FILE = "stocks.txt"
DATA_FILE = "float_data.json"
API_KEY = os.environ.get("FIRECRAWL_API_KEY")

def load_stocks():
    if os.path.exists(STOCKS_FILE):
        with open(STOCKS_FILE, "r") as f:
            return [l.strip().upper() for l in f if l.strip()]
    return []

def fetch_float_data(ticker):
    print(f"🕷️ 正在抓取 {ticker}...", end="")
    if not API_KEY:
        print(" ❌ 错误: 未配置 API Key")
        return None

    headers = {"Authorization": f"Bearer {API_KEY}"}

    # 方案 A: Finviz (HTML 模式)
    try:
        url = f"https://finviz.com/quote.ashx?t={ticker}"
        response = requests.post(
            "https://api.firecrawl.dev/v1/scrape",
            headers=headers,
            json={
                "url": url,
                "formats": ["html"],
                "mobile": False,
                "maxAge": 0
            },
            timeout=40
        )

        if response.status_code == 200:
            html = response.json().get('data', {}).get('html', '')
            match = re.search(r"Shs Float.*?([\d\.]+)([BM])", html, re.DOTALL)
            if match:
                num = float(match.group(1))
                unit = match.group(2).upper()
                val = num * (1000000 if unit == 'M' else 1000000000)
                print(f" ✅ [Finviz] 成功: {val/1000000:.2f}M")
                return val
    except Exception as e:
        print(f" (异常: {e})", end="")

    print(" ❌ 未找到数据")
    return None

if __name__ == "__main__":
    print("🚀 启动智能增量更新 (省钱版)...")
    
    database = {}
    # 1. 先读取现有的数据库
    if os.path.exists(DATA_FILE):
        with open(DATA_FILE, "r") as f:
            try: database = json.load(f)
            except: pass
            
    print(f"📘 本地已有数据: {len(database)} 条")
            
    stock_list = load_stocks()
    updated_count = 0
    
    for ticker in stock_list:
        # --- 🔥 核心修改：如果数据已存在，直接跳过 ---
        if ticker in database:
            # 只有当数据明显错误(比如是0)时才重抓，否则直接用旧的
            if database[ticker] > 0:
                print(f"📦 {ticker} 已存在，跳过 (使用缓存: {database[ticker]/1000000:.2f}M)")
                continue
        # -------------------------------------------
        
        # 只有新股票才会运行到这里
        val = fetch_float_data(ticker)
        if val:
            database[ticker] = val
            updated_count += 1
            time.sleep(1) # 只有抓取时才需要休息

    # 只有当有新数据时才写入文件
    if updated_count > 0:
        with open(DATA_FILE, "w") as f:
            json.dump(database, f, indent=4)
        print(f"🎉 更新完成！新增了 {updated_count} 条数据。")
    else:
        print("✨ 所有股票数据齐全，无需更新。")
