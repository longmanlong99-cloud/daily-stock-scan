import os
import json
import time
import re
import requests

# --- 1. 配置区 ---
STOCKS_FILE = "stocks.txt"
DATA_FILE = "float_data.json"
# 在 GitHub Actions 里，Key 会自动从环境变量读取
API_KEY = os.environ.get("FIRECRAWL_API_KEY")

def load_stocks():
    """读取股票列表"""
    if os.path.exists(STOCKS_FILE):
        with open(STOCKS_FILE, "r") as f:
            return [l.strip().upper() for l in f if l.strip()]
    return ["RDW", "RCAT", "PLTR", "TSLA", "NVDA", "AMD", "AAPL"]

def fetch_float_data(ticker):
    """
    双重抓取策略：
    1. 优先尝试 Finviz (HTML模式 + 宽松正则) -> 这是你本地测试成功的方案
    2. 失败则尝试 MarketWatch (Markdown模式) -> 备用方案
    """
    print(f"🕷️ 正在抓取 {ticker}...", end="")
    
    if not API_KEY:
        print(" ❌ 错误: 未配置 API Key")
        return None

    headers = {"Authorization": f"Bearer {API_KEY}"}

    # --- 方案 A: Finviz (HTML 模式) ---
    # 你的测试结论：这是目前唯一能抓到 RDW 85.23M 的方法
    try:
        url = f"https://finviz.com/quote.ashx?t={ticker}"
        response = requests.post(
            "https://api.firecrawl.dev/v1/scrape",
            headers=headers,
            json={
                "url": url,
                "formats": ["html"], # 👈 关键：使用 HTML 模式
                "mobile": False,
                "maxAge": 0 # 强制刷新
            },
            timeout=40
        )

        if response.status_code == 200:
            html = response.json().get('data', {}).get('html', '')
            
            # 使用你测试成功的正则：允许 Shs Float 和数字之间有任何代码
            match = re.search(r"Shs Float.*?([\d\.]+)([BM])", html, re.DOTALL)
            
            if match:
                num = float(match.group(1))
                unit = match.group(2).upper()
                val = num * (1000000 if unit == 'M' else 1000000000)
                print(f" ✅ [Finviz] 成功: {val/1000000:.2f}M")
                return val
    except Exception as e:
        print(f" (Finviz异常: {e})", end="")

    # --- 方案 B: MarketWatch (备用) ---
    try:
        mw_url = f"https://www.marketwatch.com/investing/stock/{ticker}"
        response = requests.post(
            "https://api.firecrawl.dev/v1/scrape",
            headers=headers,
            json={"url": mw_url, "formats": ["markdown"]},
            timeout=30
        )
        if response.status_code == 200:
            md = response.json().get('data', {}).get('markdown', '')
            match = re.search(r"Public Float.*?([\d\.]+)([BM])", md)
            if match:
                num = float(match.group(1))
                unit = match.group(2).upper()
                val = num * (1000000 if unit == 'M' else 1000000000)
                print(f" ✅ [MarketWatch] 补位成功: {val/1000000:.2f}M")
                return val
    except:
        pass

    print(" ❌ 所有源均未找到数据")
    return None

if __name__ == "__main__":
    print("🚀 启动精准数据更新 (HTML 穿透版)...")
    
    # 读取旧数据
    database = {}
    if os.path.exists(DATA_FILE):
        with open(DATA_FILE, "r") as f:
            try: database = json.load(f)
            except: pass
            
    stock_list = load_stocks()
    
    # 循环抓取
    for ticker in stock_list:
        val = fetch_float_data(ticker)
        if val:
            database[ticker] = val # 更新数据
            
        time.sleep(1) # 避免请求过快

    # 保存结果
    with open(DATA_FILE, "w") as f:
        json.dump(database, f, indent=4)
    
    print(f"🎉 更新完成！数据已写入 {DATA_FILE}")
