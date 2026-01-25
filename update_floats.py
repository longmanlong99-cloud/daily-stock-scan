import os
import json
import time
import re
import requests # 👈 我们直接用 requests，不再依赖 firecrawl 库

# --- 配置区 ---
STOCKS_FILE = "stocks.txt"
DATA_FILE = "float_data.json"
# 从环境变量获取 Key，确保你在 GitHub Secrets 里配置了 FIRECRAWL_API_KEY
API_KEY = os.environ.get("FIRECRAWL_API_KEY")

def load_stocks():
    if os.path.exists(STOCKS_FILE):
        with open(STOCKS_FILE, "r") as f:
            return [l.strip().upper() for l in f if l.strip()]
    return ["RDW", "RCAT", "PLTR"]

def fetch_via_direct_api(ticker):
    print(f"🕷️ [直连模式] 抓取 {ticker}...", end="")
    
    if not API_KEY:
        print(" ❌ 错误: 未配置 FIRECRAWL_API_KEY")
        return None

    url = f"https://finviz.com/quote.ashx?t={ticker}"
    
    # 构造请求头
    headers = {
        "Authorization": f"Bearer {API_KEY}",
        "Content-Type": "application/json"
    }
    
    # 构造数据包 (参考了你的截图，加了强制刷新)
    payload = {
        "url": url,
        "formats": ["markdown"],
        "onlyMainContent": False,
        "mobile": False,
        "maxAge": 0  # 👈 强制不读缓存，要最新的
    }
    
    try:
        # 直接发送 HTTP POST 请求
        response = requests.post(
            "https://api.firecrawl.dev/v1/scrape",
            headers=headers,
            json=payload,
            timeout=30
        )
        
        if response.status_code == 200:
            data = response.json()
            # 兼容不同的返回结构
            md = data.get('data', {}).get('markdown', '') or data.get('markdown', '')
            
            # --- 正则匹配 ---
            match = re.search(r"(?i)Shs Float\s*\|\s*([\d\.]+)([BM])", md)
            if match:
                num = float(match.group(1))
                unit = match.group(2).upper()
                val = num * (1000000 if unit == 'M' else 1000000000)
                print(f" ✅ 成功: {val/1000000:.2f}M")
                return val
            else:
                print(" ❌ 内容抓到了但没找到 Shs Float")
                # print(md[:200]) # 调试用
        elif response.status_code == 401:
            print(" ❌ 权限错误: API Key 无效！请检查 Secrets。")
        else:
            print(f" ❌ 请求失败: HTTP {response.status_code}")
            
    except Exception as e:
        print(f" ❌ 连接异常: {e}")
        
    return None

if __name__ == "__main__":
    print("🚀 启动精准数据更新 (HTTP 直连版)...")
    
    database = {}
    # 读取旧数据保留
    if os.path.exists(DATA_FILE):
        with open(DATA_FILE, "r") as f:
            try: database = json.load(f)
            except: pass
            
    stock_list = load_stocks()
    
    for ticker in stock_list:
        val = fetch_via_direct_api(ticker)
        if val:
            database[ticker] = val
            
        time.sleep(1) # 礼貌请求

    with open(DATA_FILE, "w") as f:
        json.dump(database, f, indent=4)
    
    print(f"🎉 更新完成！")
