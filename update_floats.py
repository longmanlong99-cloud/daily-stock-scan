import os
import json
import time
import re  # 👈 导入手术刀工具 (正则表达式)
from firecrawl import FirecrawlApp

# --- 配置区 ---
STOCKS_FILE = "stocks.txt"
DATA_FILE = "float_data.json"

# 初始化 Firecrawl (需要你在 Secrets 里配置了 FIRECRAWL_API_KEY)
app = FirecrawlApp(api_key=os.environ.get("FIRECRAWL_API_KEY"))

def load_stocks():
    if os.path.exists(STOCKS_FILE):
        with open(STOCKS_FILE, "r") as f:
            return [l.strip().upper() for l in f if l.strip()]
    return ["RDW", "RCAT", "PLTR", "TSLA", "NVDA", "AMD", "AAPL"]

def fetch_via_firecrawl(ticker):
    print(f"🕷️ Firecrawl 正在出击: {ticker}...", end="")
    
    url = f"https://finviz.com/quote.ashx?t={ticker}"
    
    try:
        # 1. 【抓取】让 Firecrawl 把网页变成 Markdown 文字
        scrape_result = app.scrape_url(url, params={'formats': ['markdown']})
        
        # 拿到那一大坨文字
        markdown_text = scrape_result['markdown']
        
        # 2. 【定位与提取】使用正则手术刀
        # 它的意思是：寻找 "Shs Float" -> 竖线 -> 数字(捕获) -> 单位M或B(捕获)
        match = re.search(r"Shs Float\s*\|\s*([\d\.]+)([BM])", markdown_text)
        
        if match:
            num_str = match.group(1) # 拿到第一个括号里的内容：85.23
            unit = match.group(2)    # 拿到第二个括号里的内容：M
            
            # 3. 【数据清洗】把 "M" 变成 1000000
            multiplier = 1000000 if unit == 'M' else 1000000000
            float_val = float(num_str) * multiplier
            
            print(f" ✅ 捕获成功: {float_val/1000000:.2f}M")
            return float_val
        else:
            print(" ❌ 没找到 'Shs Float' 字段")
            # 调试技巧：如果你想知道 Firecrawl 到底抓回了什么，可以把下面这行注释取消掉
            # print(markdown_text[:500]) 
            
    except Exception as e:
        print(f" ❌ Firecrawl 报错: {e}")
        
    return None

if __name__ == "__main__":
    print("🚀 启动 Firecrawl 增强版搬运工...")
    
    # 读取旧数据
    database = {}
    if os.path.exists(DATA_FILE):
        with open(DATA_FILE, "r") as f:
            try: database = json.load(f)
            except: pass
            
    stock_list = load_stocks()
    
    # 开始循环抓取
    for ticker in stock_list:
        val = fetch_via_firecrawl(ticker)
        
        if val:
            database[ticker] = val
            
        # 礼貌性休息 2 秒，虽然 Firecrawl 很强，但别太频繁
        time.sleep(2)

    # 保存结果
    with open(DATA_FILE, "w") as f:
        json.dump(database, f, indent=4)
    
    print(f"🎉 更新完成！Firecrawl 已将精准数据写入 {DATA_FILE}")
