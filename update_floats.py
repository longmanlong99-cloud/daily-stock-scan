import os
import json
import time
import requests
from bs4 import BeautifulSoup

# --- 配置区 ---
ALPHA_KEY = os.environ.get("ALPHA_VANTAGE_KEY")
STOCKS_FILE = "stocks.txt"
DATA_FILE = "float_data.json"

def load_stocks():
    # 读取股票清单
    if os.path.exists(STOCKS_FILE):
        with open(STOCKS_FILE, "r") as f:
            return [l.strip().upper() for l in f if l.strip()]
    return ["RDW", "RCAT", "PLTR", "TSLA", "NVDA", "AMD", "AAPL"]

# --- 🕷️ 技能1：爬取 Finviz (数据最准，优先使用) ---
def fetch_from_finviz(ticker):
    url = f"https://finviz.com/quote.ashx?t={ticker}"
    # 伪装成浏览器，防止被拦截
    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'}
    
    try:
        print(f"🕷️ 尝试爬取 Finviz: {ticker}...", end="")
        r = requests.get(url, headers=headers, timeout=10)
        
        if r.status_code == 200:
            soup = BeautifulSoup(r.text, 'html.parser')
            # 在网页里找 "Shs Float" 这一行
            for row in soup.find_all('tr'):
                cells = row.find_all('td')
                for i, cell in enumerate(cells):
                    if "Shs Float" in cell.text:
                        # 找到了！下一个格子就是数字
                        val_str = cells[i+1].text.strip()
                        
                        # 处理单位 (85.23M -> 85230000)
                        multiplier = 1
                        if val_str.endswith('M'):
                            multiplier = 1000000
                            val_str = val_str[:-1]
                        elif val_str.endswith('B'):
                            multiplier = 1000000000
                            val_str = val_str[:-1]
                        elif val_str == '-':
                            print(" ⚠️ Finviz 无数据")
                            return None
                            
                        float_val = float(val_str) * multiplier
                        print(f" ✅ 成功: {float_val/1000000:.2f}M")
                        return float_val
        print(" ❌ 没找到数据")
    except Exception as e:
        print(f" ❌ 爬取错误: {e}")
    return None

# --- 📡 技能2：问 API (作为备胎) ---
def fetch_from_alpha(ticker):
    if not ALPHA_KEY: return None
    url = f"https://www.alphavantage.co/query?function=OVERVIEW&symbol={ticker}&apikey={ALPHA_KEY}"
    try:
        print(f"📡 尝试 AlphaVantage API: {ticker}...", end="")
        r = requests.get(url)
        data = r.json()
        
        if "SharesFloat" in data and data["SharesFloat"] != "0" and data["SharesFloat"] != "None":
            float_val = float(data["SharesFloat"])
            print(f" ✅ 成功: {float_val/1000000:.2f}M")
            return float_val
    except Exception as e:
        print(f" ❌ 失败: {e}")
    return None

if __name__ == "__main__":
    print("🚀 启动超级搬运工 (Finviz + AlphaVantage)...")
    
    # 1. 读取旧数据
    database = {}
    if os.path.exists(DATA_FILE):
        with open(DATA_FILE, "r") as f:
            try: database = json.load(f)
            except: pass
            
    stock_list = load_stocks()
    print(f"📋 清单: {stock_list}")
    
    # 2. 遍历抓取
    for ticker in stock_list:
        # 优先爬 Finviz
        val = fetch_from_finviz(ticker)
        
        # 如果 Finviz 失败，才去问 API
        if not val:
            val = fetch_from_alpha(ticker)
            if val:
                # API 用了就要休息，防封号
                print("⏳ API 冷却 15秒...")
                time.sleep(15)
        else:
            # 爬虫只需要稍微休息一下
            time.sleep(2)
            
        if val:
            database[ticker] = val

    # 3. 保存结果
    with open(DATA_FILE, "w") as f:
        json.dump(database, f, indent=4)
    
    print(f"🎉 更新完成！数据已保存到 {DATA_FILE}")
