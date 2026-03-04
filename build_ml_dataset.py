import os
import time
import pandas as pd
import yfinance as yf
from datetime import timedelta

# ==========================================
# 1. 设定测试范围 (先用 3 只股票跑通管道)
# ==========================================
TEST_TICKERS = ['AAPL', 'MSFT', 'NVDA']
DATA_DIR = os.path.join(os.path.dirname(__file__), '..', 'storage', 'ml_data')

def fetch_and_align_data(tickers):
    all_aligned_data = []
    
    print(f"🚀 开始构建 ML 面板数据集，测试标的: {tickers}")
    
    for ticker in tickers:
        print(f"正在处理 {ticker}...")
        try:
            t = yf.Ticker(ticker)
            
            # 1. 拉取过去 5 年的每日价格
            prices = t.history(period="5y")
            if prices.empty:
                print(f"⚠️ {ticker} 价格数据为空，跳过。")
                continue
                
            prices = prices.reset_index()
            # 统一时区处理，避免 merge_asof 报错
            prices['Date'] = pd.to_datetime(prices['Date']).dt.tz_localize(None)
            
            # 2. 拉取季度财务数据 (yfinance 通常只提供最近 4 个季度)
            q_fin = t.quarterly_financials.T
            q_bs = t.quarterly_balance_sheet.T
            
            if q_fin.empty or q_bs.empty:
                print(f"⚠️ {ticker} 财务数据缺失，跳过。")
                continue
                
            # 合并利润表和资产负债表，去重
            q_data = pd.concat([q_fin, q_bs], axis=1)
            q_data = q_data.loc[:, ~q_data.columns.duplicated()].reset_index()
            q_data = q_data.rename(columns={'index': 'Report_Date'})
            q_data['Report_Date'] = pd.to_datetime(q_data['Report_Date']).dt.tz_localize(None)
            
            # ========================================================
            # 🌟 核心量化逻辑：处理财报发布滞后 (防止前视偏差)
            # ========================================================
            # 假设财报在季度结束后 45 天才对市场公开
            q_data['Available_Date'] = q_data['Report_Date'] + timedelta(days=45)
            
            # 按照可用日期排序，准备进行 Asof Merge
            prices = prices.sort_values('Date')
            q_data = q_data.sort_values('Available_Date')
            
            # 使用 merge_asof 将每一天的价格向后寻找"最近一次已发布的财报"
            # direction='backward' 确保只使用过去的数据
            aligned_df = pd.merge_asof(
                prices, 
                q_data, 
                left_on='Date', 
                right_on='Available_Date', 
                direction='backward'
            )
            
            # 清理无财务数据的早期价格记录
            aligned_df = aligned_df.dropna(subset=['Available_Date'])
            aligned_df['ticker'] = ticker
            
            all_aligned_data.append(aligned_df)
            
            # 礼貌性暂停，防止触发 Yahoo Finance 反爬限制
            time.sleep(1.5)
            
        except Exception as e:
            print(f"❌ 处理 {ticker} 时发生错误: {str(e)}")
            
    # 合并所有股票的数据
    if all_aligned_data:
        final_panel_df = pd.concat(all_aligned_data, ignore_index=True)
        return final_panel_df
    else:
        return pd.DataFrame()

if __name__ == "__main__":
    # 确保输出目录存在
    os.makedirs(DATA_DIR, exist_ok=True)
    
    # 运行数据提取与对齐
    df = fetch_and_align_data(TEST_TICKERS)
    
    if not df.empty:
        # 挑选几个核心列展示，证明对齐成功
        # yfinance 的财务字段名可能因公司而异，这里做宽泛抓取
        output_path = os.path.join(DATA_DIR, 'ml_training_panel_test.csv')
        df.to_csv(output_path, index=False)
        print("\n✅ 数据集构建成功！")
        print(f"📊 总行数: {len(df)}")
        print(f"💾 已保存至: {output_path}")
        print("\n数据前 3 行预览 (日期, 股票, 收盘价, 可用财报日期):")
        print(df[['Date', 'ticker', 'Close', 'Available_Date']].head(3))
    else:
        print("\n❌ 未能成功构建数据集。")
