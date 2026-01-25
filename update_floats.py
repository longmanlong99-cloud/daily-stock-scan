import os
import json
import time
import requests

# 配置区
ALPHA_KEY = os.environ.get("ALPHA_VANTAGE_KEY")
STOCKS_FILE = "stocks.txt"
DATA_FILE = "float_data.json"

# 读取你的股票清单
def load_stocks():
    # 如果有 stocks.txt 就读，没有就用默认的
    if os.path.exists(STOCKS_FILE):
        with open(STOCKS_FILE, "r") as f:
            return [l.strip().upper() for l in f if l.strip()]
    return ["RDW", "RCAT", "PLTR", "TSLA", "NVDA", "AMD", "AAPL"]

# 核心功能：去 Alpha Vantage 查户口
def fetch_float(ticker):
    url = f"https://www.alphavantage.co/query?function=OVERVIEW&symbol={ticker}&apikey={ALPHA_KEY}"
    try:
        print(f"📡 正在获取 {ticker} 的流通股数据...", end="")
        r = requests.get(url)
        data = r.json()
        
        # 1. 优先取 SharesFloat (流通股)
        if "SharesFloat" in data and data["SharesFloat"] != "0" and data["SharesFloat"] != "None":
            float_val = float(data["SharesFloat"])
            print(f" ✅ 成功: {float_val/1000000:.2f}M")
            return float_val
        
        # 2. 兜底取 SharesOutstanding (总股本)
        if "SharesOutstanding" in data:
            val = float(data["SharesOutstanding"])
            print(f" ⚠️ 仅获取到总股本: {val/1000000:.2f}M")
            return val
            
    except Exception as e:
        print(f" ❌ 失败: {e}")
    return None

if __name__ == "__main__":
    print("🚀 启动搬运工脚本...")
    
    # 读取旧数据（保留历史）
    database = {}
    if os.path.exists(DATA_FILE):
        with open(DATA_FILE, "r") as f:
            try: database = json.load(f)
            except: pass
            
    stock_list = load_stocks()
    print(f"📋 股票清单: {stock_list}")
    
    # 挨个抓取
    for ticker in stock_list:
        val = fetch_float(ticker)
        if val:
            database[ticker] = val
        
        # ⚠️ 关键：为了防止封号，每抓一个睡 15 秒
        print("⏳ 冷却 15秒...")
        time.sleep(15)

    # 保存文件
    with open(DATA_FILE, "w") as f:
        json.dump(database, f, indent=4)
    
    print(f"🎉 更新完成！数据已保存到 {DATA_FILE}")
