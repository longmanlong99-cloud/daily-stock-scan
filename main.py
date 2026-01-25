import os
import time
import requests
import yfinance as yf
from notion_client import Client

# --- 配置区 ---
POLYGON_KEY = os.environ.get("POLYGON_API_KEY")
NOTION = Client(auth=os.environ.get("NOTION_TOKEN"))
DATABASE_ID = os.environ.get("NOTION_DATABASE_ID")

# 读取清单
def load_watchlist():
    default = ["RDW", "RCAT", "PLTR", "TSLA", "NVDA", "AMD", "AAPL"]
    if os.path.exists("stocks.txt"):
        with open("stocks.txt", "r") as f:
            lines = [l.strip().upper() for l in f if l.strip()]
            if lines: return lines
    return default

WATCHLIST = load_watchlist()

def get_polygon_data(ticker):
    """
    使用 Polygon API 获取最精准的股本数据
    """
    try:
        # 1. 获取详情（拿精准股本）
        url_details = f"https://api.polygon.io/v3/reference/tickers/{ticker}?apiKey={POLYGON_KEY}"
        r = requests.get(url_details)
        if r.status_code != 200: return None
        data = r.json().get("results", {})
        
        # 获取加权流通股本 (这是最准的分母)
        shares = data.get("weighted_shares_outstanding") or data.get("share_class_shares_outstanding")
        
        # 2. 获取今日行情（拿精准成交量）
        # 这里的 prev 接口获取的是“前一个交易日”，对于盘后运行正合适
        url_price = f"https://api.polygon.io/v2/aggs/ticker/{ticker}/prev?adjusted=true&apiKey={POLYGON_KEY}"
        r2 = requests.get(url_price)
        price_data = {}
        if r2.status_code == 200 and r2.json().get("resultsCount", 0) > 0:
            res = r2.json()["results"][0]
            price_data = {
                "close": res.get("c"),
                "volume": res.get("v"),
                "high": res.get("h"),
                "low": res.get("l")
            }
            
        return {"shares": shares, "market_data": price_data}
    except Exception as e:
        print(f"⚠️ Polygon 请求失败: {e}")
        return None

def analyze_stock(ticker):
    print(f"🔍 分析: {ticker} (Polygon + Yahoo)...")
    
    # --- A. Polygon: 负责精准数据 (换手率核心) ---
    poly = get_polygon_data(ticker)
    
    # 免费版限制 5次/分，所以必须睡 15秒 防止报错
    print("   ...等待 15秒 (Polygon 免费版限制)...")
    time.sleep(15) 
    
    if not poly or not poly.get("shares"):
        print(f"⚠️ 无法从 Polygon 获取 {ticker} 股本数据，跳过")
        return None

    shares = poly["shares"]
    m_data = poly["market_data"]
    
    if not m_data:
        print(f"⚠️ 无法从 Polygon 获取 {ticker} 行情，跳过")
        return None

    # 使用 Polygon 的数据计算核心指标
    price = m_data["close"]
    volume = m_data["volume"]
    turnover_rate = volume / shares # 精准换手率
    
    # --- B. Yahoo: 负责历史趋势 (MA200, ATR) ---
    # 因为 Polygon 免费版拉历史数据很麻烦，这部分 Yahoo 依然做得很好
    try:
        yf_stock = yf.Ticker(ticker)
        hist = yf_stock.history(period="1y") # 拉长一点确保有 MA200
        if hist.empty: return None
        
        ma200 = hist['Close'].tail(200).mean()
        # 量比 (用 Polygon的今日量 / Yahoo的历史均量)
        avg_vol = hist['Volume'].tail(20).mean()
        vol_ratio = round(volume / avg_vol, 1) if avg_vol > 0 else 0
        
        # ATR 止损
        atr = (hist['High'] - hist['Low']).tail(14).mean()
        stop_loss = round(price - (2.5 * atr), 2)
    except:
        # 如果 Yahoo 挂了，给默认值
        ma200 = price * 0.9 
        vol_ratio = 1.0
        stop_loss = price * 0.9

    # --- C. 逻辑判定 ---
    price_pos = (price - m_data["low"]) / (m_data["high"] - m_data["low"]) if (m_data["high"] != m_data["low"]) else 0.5
    
    is_red_alert = False
    alert_msg = ""

    # 规则：换手率 > 20% 且 收盘位置低
    if turnover_rate > 0.20 and price_pos < 0.3:
        is_red_alert = True
        alert_msg = f"🚨 警报：高换手出货 (换手 {turnover_rate:.1%})"
    elif turnover_rate > 0.30:
        is_red_alert = True
        alert_msg = f"🚨 警报：超高换手 ({turnover_rate:.1%})"

    status = "L1-初选池"
    if price > ma200: status = "L2-观察池"
    if vol_ratio > 2.0 and price > price * 0.99: status = "L3-核心池" # 简化判定
    if is_red_alert: status = "L1-初选池"

    return {
        "price": price,
        "status": status,
        "stop": stop_loss,
        "vol": vol_ratio,
        "turnover": round(turnover_rate * 100, 2),
        "alert": is_red_alert,
        "alert_msg": alert_msg
    }

def update_notion(ticker, data):
    # 1. 查找去重
    page_id = None
    try:
        # 尝试查询
        resp = NOTION.databases.query(
            database_id=DATABASE_ID,
            filter={"property": "Name", "title": {"equals": ticker}}
        )
        if resp.get("results"):
            page_id = resp["results"][0]["id"]
    except:
        pass # 查不到就新建

    # 2. 准备内容
    tags = [{"name": data['status']}]
    if data['alert']: tags.append({"name": "🚨极端换手", "color": "red"})
    if 0.10 < data['turnover'] <= 20: tags.append({"name": "活跃/博弈", "color": "orange"})

    props = {
        "Name": {"title": [{"text": {"content": ticker}}]},
        "Status": {"select": {"name": data['status']}},
        "Tags": {"multi_select": tags}
    }
    
    # 正文块
    content = [
        {"object": "block", "type": "paragraph", "paragraph": {"rich_text": [
            {"text": {"content": f"💰 现价(Poly): ${data['price']} | 🛡️ 止损: ${data['stop']}\n"}},
            {"text": {"content": f"📊 精准换手: {data['turnover']}% | 量比: {data['vol']}x\n"}},
            {"text": {"content": f"{data['alert_msg']}" if data['alert'] else ""}, "annotations": {"color": "red"}}
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
        print(f"❌ Notion 推送失败: {e}")

if __name__ == "__main__":
    print("🚀 启动 Polygon 增强版扫描...")
    if not POLYGON_KEY:
        print("❌ 错误：未找到 POLYGON_API_KEY，请在 GitHub Secrets 中配置！")
    else:
        for t in WATCHLIST:
            data = analyze_stock(t)
            if data:
                update_notion(t, data)
    print("🏁 完成")
