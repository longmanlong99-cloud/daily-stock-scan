import json
import os
import sys

# ==========================================
# 1. 策略配置 (Strategy Config)
# ==========================================
CONFIG = {
    "LARGE_CAP_THRESHOLD": 10_000_000_000, # 100亿定义为大盘
    "LARGE_CAP_TURNOVER_LIMIT": 0.05,      # 大盘股 > 5% 换手 = 危险
    "SMALL_CAP_TURNOVER_LIMIT": 0.20,      # 小盘股 > 20% 换手 = 危险 (RDW 59% 会被杀)
    "RSI_MAX_LIMIT": 75,                   # RSI > 75 = 过热
    "DAILY_DATA_FILE": "daily_cache.json",
    "FLOAT_DATA_FILE": "float_data.json"
}

# ==========================================
# 2. 工具函数
# ==========================================
def load_json_data(filepath):
    if not os.path.exists(filepath):
        print(f"❌ 错误: 找不到 {filepath}")
        return {}
    with open(filepath, "r", encoding="utf-8") as f:
        return json.load(f)

# ==========================================
# 3. 核心归类逻辑 (Core Logic)
# ==========================================
def classify_stock(ticker, data, shares_float):
    
    # --- 1. 数据解包 ---
    price = data.get('price', 0)
    rsi = data.get('rsi', 50)
    volume = data.get('volume', 0)
    
    # 获取均线系统 (由 fetch_data.py 提供)
    ma20 = data.get('ma20', 0)
    ma60 = data.get('ma60', 0)
    ma200 = data.get('ma200', 0)
    
    if price == 0 or shares_float == 0:
        return {"status": "数据缺失", "style": "⚪", "reason": "无法计算", "turnover": 0}

    # --- 2. 计算核心指标 ---
    market_cap = price * shares_float
    turnover_rate = volume / shares_float 

    # =======================================================
    # 🚨 逻辑层 A：死亡换手熔断 (The Circuit Breaker)
    # =======================================================
    # 目的：不管趋势多好，只要换手率炸了，直接判死刑 (L3)
    
    is_high_risk = False
    risk_msg = ""

    if market_cap > CONFIG["LARGE_CAP_THRESHOLD"]:
        # 大盘股逻辑
        if turnover_rate > CONFIG["LARGE_CAP_TURNOVER_LIMIT"]:
            is_high_risk = True
            risk_msg = f"大盘股放量滞涨风险 ({turnover_rate*100:.1f}%)"
    else:
        # 小盘股逻辑 (RDW 案例在此)
        # RDW (市值8亿) + 换手 59% -> 0.59 > 0.20 -> 触发熔断
        if turnover_rate > CONFIG["SMALL_CAP_TURNOVER_LIMIT"]:
            is_high_risk = True
            risk_msg = f"小盘股死亡换手 ({turnover_rate*100:.1f}%)"

    if is_high_risk:
        return {
            "status": "L3-高危/异常", 
            "style": "🚨", 
            "reason": risk_msg,
            "turnover": turnover_rate
        }

    # =======================================================
    # 🔍 逻辑层 B：L2 晋级判断 (Selection Filter)
    # =======================================================
    # 只有通过了逻辑层 A 的股票才会运行到这里
    # 定义：L2 观察池 = 趋势向上(MA60) + 形态健康(RSI)
    
    # 判据 A: 长期趋势 (必须站上 MA60 生命线)
    trend_ok = price > ma60
    
    # 判据 B: RSI 健康 (没有极度超买)
    rsi_ok = rsi < CONFIG["RSI_MAX_LIMIT"]
    
    # (可选) 判据 C: 短期强势 (站上 MA20)
    # 仅作为备注参考
    short_term_strong = price > ma20

    if trend_ok and rsi_ok:
        # 进入 L2 观察池
        reason_str = "趋势向上(>MA60)"
        if not short_term_strong:
            reason_str += " 但短期回踩(<MA20)"
        else:
            reason_str += " 且短期强势(>MA20)"
            
        return {
            "status": "L2-观察池", 
            "style": "🟢", 
            "reason": reason_str,
            "turnover": turnover_rate
        }

    # =======================================================
    # 🗑️ 默认归类 (L3)
    # =======================================================
    # 跌破 MA60，或者 RSI 过热
    fail_reason = "弱势"
    if not trend_ok:
        fail_reason = "跌破中期趋势(MA60)"
    elif not rsi_ok:
        fail_reason = f"RSI过热({rsi})"
        
    return {
        "status": "L3-弱势/观望", 
        "style": "💤", 
        "reason": fail_reason,
        "turnover": turnover_rate
    }

# ==========================================
# 4. 主程序
# ==========================================
def main():
    print("\n=== 🚀 执行股票归类 (含逻辑层 A - RDW 杀手) ===")
    
    # 加载数据
    daily_cache = load_json_data(CONFIG["DAILY_DATA_FILE"])
    float_cache = load_json_data(CONFIG["FLOAT_DATA_FILE"])
    
    if not daily_cache or not float_cache:
        print("⚠️ 数据文件缺失，请先运行 fetch_data.py 和 update_floats.py")
        return

    print(f"{'代码':<8} {'现价':<10} {'MA60':<10} {'换手率':<10} {'归类结果':<15} {'详细理由'}")
    print("-" * 85)

    for ticker, stock_data in daily_cache.items():
        s_float = float_cache.get(ticker, 0)
        
        # 排除无股本数据的
        if s_float == 0: 
            continue
            
        # 执行分类
        res = classify_stock(ticker, stock_data, s_float)
        
        # 格式化输出
        p_price = f"${stock_data.get('price')}"
        p_ma60 = f"${stock_data.get('ma60')}"
        p_turn = f"{res['turnover']*100:.1f}%"
        p_status = f"{res['style']} {res['status']}"
        
        print(f"{ticker:<8} {p_price:<10} {p_ma60:<10} {p_turn:<10} {p_status:<15} {res['reason']}")

    print("-" * 85)
    print("✅ 扫描完成")

if __name__ == "__main__":
    main()
