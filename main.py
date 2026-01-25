import os
import json
import yfinance as yf
import pandas as pd
from notion_client import Client
from datetime import datetime

# --- 1. 基础配置 ---
WATCHLIST = ["RDW", "RCAT", "PLTR", "TSLA", "NVDA", "AMD", "AAPL"]
notion = Client(auth=os.environ.get("NOTION_TOKEN"))
database_id = os.environ.get("NOTION_DATABASE_ID")

# --- 🔥 新增：智能读取 "搬运工" 抓下来的精准数据 ---
# 这样就不需要你在代码里死编 RDW=8500万了
# 代码会自动去读 float_data.json，如果读到了 RDW，就用那个准的
FLOAT_DB = {}
if os.path.exists("float_data.json"):
    try:
        with open("float_data.json", "r") as f:
            FLOAT_DB = json.load(f)
        print(f"📘 已加载本地精准数据: {len(FLOAT_DB)} 条")
    except Exception as e:
        print(f"⚠️ 读取 float_data.json 失败: {e}")

def get_stock_logic(ticker):
    print(f"🔍 深度扫描: {ticker}...")
    try:
        stock = yf.Ticker(ticker)
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
        
        # --- ⚖️ 核心修改：分母(股本)取值逻辑 ---
        shares = 0
        
        # 1. 第一优先级：看本地库 (float_data.json)
        # 如果搬运工抓到了 RDW=85M，这里就会自动用上，换手率就是 60%
        if ticker in FLOAT_DB:
            shares = FLOAT_DB[ticker]
            # print(f"   🎯 使用本地精准股本: {shares/1000000:.2f}M")
            
        # 2. 第二优先级：问 Yahoo (floatShares)
        if not shares:
            shares = stock.info.get('floatShares')
            
        # 3. 第三优先级：问 Yahoo (sharesOutstanding)
        if not shares:
            shares = stock.info.get('sharesOutstanding')

        # 计算换手率
        turnover_rate = (volume / shares) if shares else 0
        
        # 计算量比
        avg_vol = hist['Volume'].mean()
        vol_ratio = round(volume / avg_vol, 1) if avg_vol > 0 else 0
        ma_close = hist['Close'].mean()
        
        # --- 风险判定 ---
        price_pos = (price - low_p) / (high_p - low_p) if (high_p - low_p) != 0 else 0.5
        is_red_alert = False
        alert_msg = ""
        
        if turnover_rate > 0.5 and price_pos < 0.3:
            is_red_alert = True
            alert_msg = f"🚨 警报：天量出货 (换手 {turnover_rate:.1%})"
        elif turnover_rate > 0.6:
            is_red_alert = True
            alert_msg = f"🚨 警报：极端换手 ({turnover_rate:.1%})"

        # --- 评级 ---
        status = "L1-初选池"
        if price > ma_close: status = "L2-观察池"
        if vol_ratio > 2.0 and price > open_p: status = "L3-核心池"
        if is_red_alert: status = "L1-初选池"

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
    """更新 Notion 数据"""
    try:
        # --- 🔥 新增：时间戳 ---
        # 格式：01-25 14:30
        time_str = datetime.now().strftime("%m-%d %H:%M")

        # 1. 尝试查询 ID (带容错)
        page_id = None
        try:
            # 注意：如果 notion 库版本不对，这行 query 可能会报错
            # 我们用 try 包裹，如果报错就直接跳过去创建新页面
            response = notion.databases.query(
                database_id=database_id,
                filter={"property": "Name", "title": {"equals": ticker}}
            )
            if response and response.get("results"):
                page_id = response["results"][0]["id"]
        except Exception:
            # 如果查询失败（比如库版本不对），不打印烦人的报错，直接当做没找到，去新建
            pass

        # 2. 准备数据
        tags = [{"name": data['status']}]
        if data['alert']: tags.append({"name": "🚨极端换手", "color": "red"})
        
        properties = {
            "Name": {"title": [{"text": {"content": ticker}}]},
            "Status": {"select": {"name": data['status']}},
            "Tags": {"multi_select": tags}
        }
        
        # 正文内容 (加入了时间戳)
        content_block = {
            "object": "block", 
            "type": "paragraph", 
            "paragraph": {
                "rich_text": [
                    {"text": {"content": f"💰 现价: ${data['price']} | 🛡️ 止损: ${data['stop']}\n"}},
                    {"text": {"content": f"📊 换手: {data['turnover']}% | 量比: {data['vol']}x\n"}},
                    {"text": {"content": f"🕒 更新: {time_str}  ", "annotations": {"color": "gray", "italic": True}}},
                    {"text": {"content": f"{data['alert_msg']}" if data['alert'] else "", "annotations": {"color": "red"}}}
                ]
            }
        }

        # 3. 执行
        if page_id:
            notion.pages.update(page_id=page_id, properties=properties)
            # 尝试追加内容块，如果失败就算了
            try: notion.blocks.children.append(block_id=page_id, children=[content_block])
            except: pass
            print(f"🔄 {ticker} 更新成功")
        else:
            notion.pages.create(
                parent={"database_id": database_id}, 
                properties=properties, 
                children=[content_block]
            )
            print(f"✨ {ticker} 创建成功")
            
    except Exception as e:
        print(f"❌ {ticker} 推送 Notion 最终失败: {e}")

if __name__ == "__main__":
    # 删除了会导致报错的 version 打印
    print("🚀 开始执行每日选股任务...")
    
    for t in WATCHLIST:
        res = get_stock_logic(t)
        if res:
            update_notion(t, res)
            
    print("🏁 任务完成！")
