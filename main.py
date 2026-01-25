import os
import yfinance as yf
import pandas as pd
from notion_client import Client
import notion_client

# --- 1. 基础配置 ---
WATCHLIST = ["RDW", "RCAT", "PLTR", "TSLA", "NVDA", "AMD", "AAPL"]
notion = Client(auth=os.environ.get("NOTION_TOKEN"))
database_id = os.environ.get("NOTION_DATABASE_ID")

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
        
        # 尝试获取总股本计算换手率，如果失败则给默认值
        try:
            shares = stock.info.get('sharesOutstanding')
            turnover_rate = (volume / shares) if shares else 0
        except:
            turnover_rate = 0
        
        # 计算量比 (当日成交量 / 过去5日均量，避免长期平均拉低敏感度)
        avg_vol = hist['Volume'].mean()
        vol_ratio = round(volume / avg_vol, 1) if avg_vol > 0 else 0
        ma_close = hist['Close'].mean()
        
        # --- 风险判定逻辑 (覆盖 RDW 61% 换手率) ---
        price_pos = (price - low_p) / (high_p - low_p) if (high_p - low_p) != 0 else 0.5
        
        is_red_alert = False
        alert_msg = ""
        
        # 规则：换手率 > 50% 且收盘价接近最低点
        if turnover_rate > 0.5 and price_pos < 0.3:
            is_red_alert = True
            alert_msg = f"🚨 警报：天量出货 (换手 {turnover_rate:.1%})"
        elif turnover_rate > 0.6:
            is_red_alert = True
            alert_msg = f"🚨 警报：极端换手 ({turnover_rate:.1%})"

        # --- 漏斗分级逻辑 ---
        status = "L1-初选池"
        if price > ma_close: status = "L2-观察池"
        if vol_ratio > 2.0 and price > open_p: status = "L3-核心池"
        
        # 风险强制降级
        if is_red_alert: status = "L1-初选池"

        # ATR 简单估算
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
        print(f"❌ {ticker} 数据计算出错: {e}")
        return None

def update_notion(ticker, data):
    """更新 Notion 数据 (含防报错机制)"""
    try:
        # 1. 尝试查询是否已有该股票 (修复 AttributeError 问题)
        page_id = None
        try:
            # 标准查询接口
            response = notion.databases.query(
                database_id=database_id,
                filter={"property": "Name", "title": {"equals": ticker}}
            )
            if response and response.get("results"):
                page_id = response["results"][0]["id"]
        except Exception as query_err:
            print(f"⚠️ 查询 {ticker} 失败，将直接尝试创建新条目: {query_err}")

        # 2. 准备数据
        tags = [{"name": data['status']}]
        if data['alert']: tags.append({"name": "🚨极端换手", "color": "red"})
        
        # 这里的 key 必须对应 Notion 里的列名
        properties = {
            "Name": {"title": [{"text": {"content": ticker}}]},
            "Status": {"select": {"name": data['status']}}, # 对应你改好的 Select 类型
            "Tags": {"multi_select": tags}
        }
        
        # 正文内容
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

        # 3. 执行更新或创建
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
        # 这里会打印详细的 Notion 报错，方便定位
        print(f"❌ {ticker} 推送 Notion 最终失败: {e}")

if __name__ == "__main__":
    print(f"🛠️ Notion Client Version: {notion_client.__version__}")
    print("🚀 开始执行每日选股任务...")
    
    for t in WATCHLIST:
        res = get_stock_logic(t)
        if res:
            update_notion(t, res)
            
    print("🏁 任务完成！")
