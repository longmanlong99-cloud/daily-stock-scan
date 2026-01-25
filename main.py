import os
import time
import requests
import yfinance as yf
from notion_client import Client

# --- 配置区 ---
POLYGON_KEY = os.environ.get("POLYGON_API_KEY")
ALPHA_KEY = os.environ.get("ALPHA_VANTAGE_KEY") # 新钥匙
NOTION = Client(auth=os.environ.get("NOTION_TOKEN"))
DATABASE_ID = os.environ.get("NOTION_DATABASE_ID")

def load_watchlist():
    # 只需要维护 stocks.txt 即可，代码会自动适应
    default = ["RDW", "RCAT", "PLTR", "TSLA", "NVDA", "AMD", "AAPL"]
    if os.path.exists("stocks.txt"):
        with open("stocks.txt", "r") as f:
            lines = [l.strip().upper() for l in f if l.strip()]
            if lines: return lines
    return default

WATCHLIST = load_watchlist()

def get_float_shares(ticker):
    """
    使用 Alpha Vantage 获取精准流通股本 (Float Shares)
    """
    try:
        url = f"https://www.alphavantage.co/query?function=OVERVIEW&symbol={ticker}&apikey={ALPHA_KEY}"
        r = requests.get(url)
        data = r.json()
        
        # 获取 SharesFloat (流通股)
        # 注意：有时候 API 返回的是 'None' 或 '0'，需要兜底
        if "SharesFloat" in data and data["SharesFloat"] != "None":
            float_shares = float(data["SharesFloat"])
            print(f"   📘 AlphaVantage 流通股: {float_shares/1000000:.2f}M")
            return float_shares
        
        # 如果 AlphaVantage 没查到（偶发），尝试用 SharesOutstanding 兜底
        if "SharesOutstanding" in data and data["SharesOutstanding"] != "None":
            print(f"   ⚠️ 降级使用总股本: {data['SharesOutstanding']}")
            return float(data["SharesOutstanding"])
            
    except Exception as e:
        print(f"   ⚠️ AlphaVantage 请求失败: {e}")
    
    return None

def get_data(ticker):
    print(f"🔍 全自动扫描: {ticker}...")
    
    # 1. 获取分子：Polygon 实时成交量
    # 必须睡 15秒 (AlphaVantage 限制每分钟5次，Polygon 也是，所以15秒完美)
    time.sleep(15)
    
    poly_volume = 0
    poly_price = 0
    
    try:
        url = f"https://api.polygon.io/v2/aggs/ticker/{ticker}/prev?adjusted=true&apiKey={POLYGON_KEY}"
        r = requests.get(url)
        if r.status_code == 200 and r.json().get("resultsCount", 0) > 0:
            res = r.json()["results"][0]
            poly_volume = res.get("v", 0)
            poly_price = res.get("c", 0)
    except Exception as e:
        print(f"   ❌ Polygon 失败: {e}")
        return None

    # 2. 获取分母：Alpha Vantage 流通股
    share_float = get_float_shares(ticker)
    
    # 双重保险：如果两个 API 都没拿到分母，用 Yahoo 最后的挣扎
    if not share_float:
        try:
            stock = yf.Ticker(ticker)
            share_float = stock.info.get('floatShares') or stock.info.get('sharesOutstanding')
        except: pass

    # 3. 计算换手率
    if not share_float or share_float == 0:
        print(f"   ❌ 无法获取分母，跳过计算")
        turnover_rate = 0
    else:
        turnover_rate = poly_volume / share_float
        print(f"   📊 换手率: {turnover_rate:.2%}")

    # 4. 辅助指标 (均线/量比/止损) - 使用 Yahoo 历史数据
    ma200 = poly_price 
    vol_ratio = 1.0
    stop_loss = poly_price * 0.9
    
    try:
        hist_long = yf.Ticker(ticker).history(period="1y")
        if not hist_long.empty:
            ma200 = hist_long['Close'].tail(200).mean()
            avg_vol = hist_long['Volume'].tail(20).mean()
            vol_ratio = round(poly_volume / avg_vol, 1) if avg_vol > 0 else 0
            atr = (hist_long['High'] - hist_long['Low']).tail(14).mean()
            stop_loss = round(poly_price - (2.5 * atr), 2)
    except: pass

    # 5. 警报判定
    is_red_alert = False
    alert_msg = ""
    
    if turnover_rate > 0.20:
        is_red_alert = True
        alert_msg = f"🚨 警报：高换手出货 (换手 {turnover_rate:.1%})"

    status = "L1-初选池"
    if not is_red_alert: status = "L2-观察池"

    return {
        "price": poly_price,
        "status": status,
        "stop": stop_loss,
        "vol": vol_ratio,
        "turnover": round(turnover_rate * 100, 2),
        "alert": is_red_alert,
        "alert_msg": alert_msg
    }

def update_notion(ticker, data):
    page_id = None
    try:
        resp = NOTION.databases.query(
            database_id=DATABASE_ID,
            filter={"property": "Name", "title": {"equals": ticker}}
        )
        if resp.get("results"):
            page_id = resp["results"][0]["id"]
    except: pass 

    tags = [{"name": data['status']}]
    if data['alert']: tags.append({"name": "🚨极端换手", "color": "red"})
    
    props = {
        "Name": {"title": [{"text": {"content": ticker}}]},
        "Status": {"select": {"name": data['status']}},
        "Tags": {"multi_select": tags}
    }
    
    content = [
        {"object": "block", "type": "paragraph", "paragraph": {"rich_text": [
            {"text": {"content": f"💰 现价: ${data['price']} | 🛡️ 止损: ${data['stop']:.2f}\n"}},
            {"text": {"content": f"📊 换手(自动): {data['turnover']}% | ⚠️ {data['alert_msg'] if data['alert'] else '正常'}\n"}}
        ]}}
    ]

    try:
        if page_id:
            NOTION.pages.update(page_id=page_id, properties=props)
            print(f"✅ {ticker} 更新成功")
        else:
            NOTION.pages.create(parent={"database_id": DATABASE_ID}, properties=props, children=content)
            print(f"✨ {ticker} 新建成功")
    except Exception as e:
        print(f"❌ Notion 失败: {e}")

if __name__ == "__main__":
    print("🚀 启动全自动扫描 (Polygon + AlphaVantage)...")
    if not POLYGON_KEY or not ALPHA_KEY:
        print("❌ 错误：请确保 POLYGON_API_KEY 和 ALPHA_VANTAGE_KEY 都已配置！")
    else:
        for t in WATCHLIST:
            data = get_data(t)
            if data:
                update_notion(t, data)
    print("🏁 完成")
