import os
import yfinance as yf
import pandas as pd
from notion_client import Client

# --- 1. 基础配置 ---
WATCHLIST = ["RDW", "RCAT", "PLTR", "TSLA", "NVDA", "AMD", "AAPL"]
notion = Client(auth=os.environ.get("NOTION_TOKEN"))
database_id = os.environ.get("NOTION_DATABASE_ID")

def get_stock_logic(ticker):
    """
    核心筛选规则：
    L1 初选：默认池
    L2 观察：站上200日均线 (趋势转强)
    L3 核心：量比 > 2x 且收阳线 (主力介入)
    红色警报：换手率 > 50% 且收盘接近全天最低点 (天量出货)
    """
    print(f"🔍 深度扫描: {ticker}...")
    try:
        stock = yf.Ticker(ticker)
        # 增加获取 info 以便计算换手率
        info = stock.info
        hist = stock.history(period="1y")
        if hist.empty: return None

        # 获取基础价格数据
        price = round(hist['Close'].iloc[-1], 2)
        open_p = hist['Open'].iloc[-1]
        low_p = hist['Low'].iloc[-1]
        high_p = hist['High'].iloc[-1]
        volume = hist['Volume'].iloc[-1]
        
        # 1. 计算换手率 (当日成交量 / 总股本)
        shares = info.get('sharesOutstanding')
        turnover_rate = (volume / shares) if shares else 0
        
        # 2. 计算量比 (当日成交量 / 20日均量)
        vol_ratio = round(volume / hist['Volume'].tail(20).mean(), 1)
        ma200 = hist['Close'].tail(200).mean()
        
        # 3. 风险判定逻辑 (针对 RDW 61% 换手率的情况)
        # 判断收盘价是否在全天波动的底部 (低于20%的位置)
        price_pos = (price - low_p) / (high_p - low_p) if (high_p - low_p) != 0 else 0.5
        
        is_red_alert = False
        alert_msg = ""
        # 换手率 > 50% 且收盘接近最低点 (天量出货)
        if turnover_rate > 0.5 and price_pos < 0.2:
            is_red_alert = True
            alert_msg = f"🚨 警报：天量出货 (换手 {turnover_rate:.1%})"
        # 换手率极端 > 30% 且收阴线
        elif turnover_rate > 0.6 and price < open_p:
            is_red_alert = True
            alert_msg = f"🚨 警报：极端换手风险 ({turnover_rate:.1%})"

        # 4. 漏斗分级逻辑
        status = "L1-初选池"
        if price > ma200: status = "L2-观察池"
        if vol_ratio > 2.0 and price > open_p: status = "L3-核心池"
        
        # 触发风险警报则强制降级回 L1
        if is_red_alert: status = "L1-初选池"

        # 5. ATR 动态止损 (2.5倍波动空间)
        atr = (hist['High'] - hist['Low']).rolling(14).mean().iloc[-1]
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
        print(f"❌ {ticker} 数据抓取失败: {e}")
        return None

def update_notion(ticker, data):
    """更新 Notion 数据并实现自动去重更新"""
    try:
        # 使用官方推荐的查询方式检查是否已有该股票
        search_results = notion.databases.query(
            database_id=database_id,
            filter={"property": "Name", "title": {"equals": ticker}}
        ).get("results")

        # 准备标签和内容
        tags = [{"name": data['status']}]
        if data['alert']: tags.append({"name": "🚨极端换手", "color": "red"})
        if data['turnover'] > 20: tags.append({"name": "高波动/博弈", "color": "orange"})

        properties = {
            "Name": {"title": [{"text": {"content": ticker}}]},
            "Status": {"select": {"name": data['status']}},
            "Tags": {"multi_select": tags}
        }
        
        # 构建卡片正文详情
        content_text = f"💰 现价: ${data['price']} | 🛡️ 止损: ${data['stop']}\n📊 换手: {data['turnover']}% | 量比: {data['vol']}x"
        if data['alert']:
            content_text += f"\n{data['alert_msg']}"

        rich_text_children = [{"object": "block", "type": "paragraph", "paragraph": {"rich_text": [
            {"text": {"content": content_text}}
        ]}}]

        if search_results:
            # 已有该股票，更新数据和位置
            page_id = search_results[0]["id"]
            notion.pages.update(page_id=page_id, properties=properties)
            print(f"🔄 {ticker} 已同步更新")
        else:
            # 新股票入库
            notion.pages.create(parent={"database_id": database_id}, properties=properties, children=rich_text_children)
            print(f"✨ {ticker} 已成功入库")
    except Exception as e:
        print(f"❌ {ticker} 推送 Notion 失败: {e}")

if __name__ == "__main__":
    print("🚀 开始执行每日选股任务...")
    for t in WATCHLIST:
        res = get_stock_logic(t)
        if res:
            update_notion(t, res)
    print("🏁 任务完成！")
