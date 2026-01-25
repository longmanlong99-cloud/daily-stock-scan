import os
import yfinance as yf
import pandas as pd
from notion_client import Client

# --- 核心配置 ---
# 1. Notion 客户端初始化
notion = Client(auth=os.environ.get("NOTION_TOKEN"))
database_id = os.environ.get("NOTION_DATABASE_ID")

# 2. 读取外部股票清单函数
def load_watchlist():
    """
    优先读取 stocks.txt 文件。
    如果文件不存在，使用默认列表。
    """
    default_list = ["AAPL", "NVDA", "TSLA"] # 兜底列表
    file_path = "stocks.txt"
    
    try:
        if os.path.exists(file_path):
            with open(file_path, "r") as f:
                # 读取每一行，去除空格和换行符，并过滤空行
                lines = [line.strip().upper() for line in f if line.strip()]
            if lines:
                print(f"📂 成功加载外部清单 ({len(lines)} 只): {lines}")
                return lines
    except Exception as e:
        print(f"⚠️ 读取 stocks.txt 出错: {e}")
    
    print("⚠️ 未找到有效 stocks.txt，使用内置默认列表。")
    return default_list

# 获取监控名单
WATCHLIST = load_watchlist()

def get_stock_logic(ticker):
    print(f"🔍 深度扫描: {ticker}...")
    try:
        stock = yf.Ticker(ticker)
        # 强制刷新数据
        hist = stock.history(period="5d") 
        if hist.empty: 
            print(f"⚠️ {ticker} 无法获取K线数据")
            return None

        # 获取基础数据
        price = round(hist['Close'].iloc[-1], 2)
        open_p = hist['Open'].iloc[-1]
        low_p = hist['Low'].iloc[-1]
        high_p = hist['High'].iloc[-1]
        volume = hist['Volume'].iloc[-1]
        
        # --- 精确度升级：优先使用流通股本计算换手率 ---
        try:
            # 优先取 floatShares (流通股)，如果没有则取 sharesOutstanding (总股本)
            shares = stock.info.get('floatShares') or stock.info.get('sharesOutstanding')
            turnover_rate = (volume / shares) if shares else 0
        except:
            turnover_rate = 0
        
        # 计算量比
        avg_vol = hist['Volume'].mean()
        vol_ratio = round(volume / avg_vol, 1) if avg_vol > 0 else 0
        ma_close = hist['Close'].mean()
        
        # --- 风险判定逻辑 (阈值 20%) ---
        price_pos = (price - low_p) / (high_p - low_p) if (high_p - low_p) != 0 else 0.5
        is_red_alert = False
        alert_msg = ""
        
        # 规则 A：换手率 > 20% 且收盘价接近最低点 (出货嫌疑)
        if turnover_rate > 0.20 and price_pos < 0.3:
            is_red_alert = True
            alert_msg = f"🚨 警报：高换手出货 (换手 {turnover_rate:.1%})"
            
        # 规则 B：换手率 > 30% (极端活跃)
        elif turnover_rate > 0.30:
            is_red_alert = True
            alert_msg = f"🚨 警报：超高换手 ({turnover_rate:.1%})"

        # --- 漏斗分级 ---
        status = "L1-初选池"
        if price > ma_close: status = "L2-观察池"
        if vol_ratio > 2.0 and price > open_p: status = "L3-核心池"
        
        # 风险强制降级
        if is_red_alert: status = "L1-初选池"

        # ATR 止损估算
        atr = (hist['High'] - hist['Low']).mean()
        stop_loss = round(price - (2.5 * atr), 2)

        return {
            "price": price, 
            "status": status, 
            "stop": stop_loss, 
            "vol": vol_ratio, 
            "turnover": round(turnover_rate * 100, 2),
            "alert": is_red_alert,
            "alert_msg": alert_msg
        }
    except Exception as e:
        print(f"❌ {ticker} 计算出错: {e}")
        return None

def update_notion(ticker, data):
    """更新 Notion 数据 (含环境兼容保护)"""
    page_id = None
    
    # 1. 尝试查询去重
    try:
        response = notion.databases.query(
            database_id=database_id,
            filter={"property": "Name", "title": {"equals": ticker}}
        )
        if response and response.get("results"):
            page_id = response["results"][0]["id"]
    except AttributeError:
        print(f"⚠️ 环境 Notion 库版本过旧，跳过去重步骤，转为直接创建。")
    except Exception as e:
        print(f"⚠️ 查询 {ticker} 失败 ({e})，转为创建模式。")

    # 2. 准备数据
    tags = [{"name": data['status']}]
    if data['alert']: tags.append({"name": "🚨极端换手", "color": "red"})
    if 0.10 < data['turnover'] <= 20: tags.append({"name": "活跃/博弈", "color": "orange"})
    
    properties = {
        "Name": {"title": [{"text": {"content": ticker}}]},
        "Status": {"select": {"name": data['status']}}, 
        "Tags": {"multi_select": tags}
    }
    
    content_block = {
        "object": "block", 
        "type": "paragraph", 
        "paragraph": {
            "rich_text": [
                {"text": {"content": f"💰 现价: ${data['price']} | 🛡️ 止损: ${data['stop']}\n"}},
                {"text": {"content": f"📊 换手: {data['turnover']}% | 量比: {data['vol']}x\n"}},
                {"text": {"content": f"{data['alert_msg']}" if data['alert'] else ""}, "annotations": {"color": "red"}}
            ]
        }
    }

    # 3. 执行更新
    try:
        if page_id:
            notion.pages.update(page_id=page_id, properties=properties)
            print(f"🔄 {ticker} 更新成功")
        else:
            notion.pages.create(
                parent={"database_id": database_id}, 
                properties=properties, 
                children=[content_block]
            )
            print(f"✨ {ticker} 创建成功")
    except Exception as e:
        print(f"❌ {ticker} 推送失败: {e}")

if __name__ == "__main__":
    print("🚀 开始运行...")
    if not WATCHLIST:
        print("❌ 监控名单为空，请检查 stocks.txt")
    else:
        for t in WATCHLIST:
            res = get_stock_logic(t)
            if res:
                update_notion(t, res)
    print("🏁 完成！")
