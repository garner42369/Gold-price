import streamlit as st
import streamlit.components.v1 as components
import akshare as ak
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots
from datetime import datetime, timedelta
import os
import re
import requests
import pytz
import logging
import time  # 引入时间模块处理物理冷却时间

# --- 1. Streamlit 页面配置 (必须放在最开头) ---
st.set_page_config(page_title="SGE Gold Terminal", layout="wide", initial_sidebar_state="collapsed")

# --- 2. 日志配置 (精简版，适应云端) ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
logger = logging.getLogger(__name__)

beijing_tz = pytz.timezone('Asia/Shanghai')

# ==========================================
# 静态资源与常量定义 (前端黑科技)
# ==========================================
indicator_info = {
    'Price': {'name': '收盘价', 'meaning': '当日最后一笔成交价格', 'action': '反映市场最终共识。'},
    'MA5': {'name': '5日均线', 'meaning': '5个交易日均价', 'action': '短期趋势参考。'},
    'MA10': {'name': '10日均线', 'meaning': '10个交易日均价', 'action': '中短期多空分水岭。'},
    'MA20': {'name': '20日均线', 'meaning': '20个交易日均价', 'action': '中期生命线判断。'},
    'DIF': {'name': 'DIF', 'meaning': '12日与26日EMA之差', 'action': '反映价格变动速度。'},
    'DEA': {'name': 'DEA', 'meaning': 'DIF的9日平均', 'action': '用于平滑DIF波动。'},
    'MACD': {'name': 'MACD', 'meaning': '(DIF-DEA)*2', 'action': '直观显示多空强弱。'}
}

help_modal_html = """
<style>
html, body { margin: 0; height: 100%; }
.js-plotly-plot { height: 100vh !important; }
</style>
<div id='customModal' style='position:fixed;top:0;left:0;width:100%;height:100%;background:rgba(0,0,0,0.7);display:flex;justify-content:center;align-items:center;z-index:9999;'>
    <div style='background:#1a1a1a;padding:25px;border-radius:12px;max-width:500px;width:90%;color:#ffffff;position:relative;font-family:sans-serif;box-shadow:0 10px 30px rgba(0,0,0,0.5);'>
        <span onclick='document.getElementById(\"customModal\").style.display=\"none\"' style='position:absolute;top:10px;right:15px;cursor:pointer;font-size:24px;color:#888;'>&times;</span>
        <h3 style='margin-top:0;color:#00ffcc;'>指标解释 (Indicators Guide)</h3>
        <div style='line-height:1.6;font-size:14px;'>
"""
for key, info in indicator_info.items():
    help_modal_html += f"<p><b>{info['name']}</b>: {info['action']}</p>"
help_modal_html += """
        </div>
        <button onclick='document.getElementById("customModal").style.display="none"' style='margin-top:20px;padding:8px 20px;background:#00ffcc;border:none;border-radius:4px;cursor:pointer;font-weight:bold;color:#1a1a1a;'>知道了</button>
    </div>
</div>
<script>
(function() {
    const buildTooltip = () => {
        const tip = document.createElement('div');
        tip.id = 'customHoverTip';
        tip.style.position = 'absolute';
        tip.style.zIndex = '99999';
        tip.style.pointerEvents = 'none';
        tip.style.background = 'rgba(0,0,0,0.6)';
        tip.style.color = 'rgba(255, 77, 77, 0.9)';
        tip.style.fontFamily = 'Courier New';
        tip.style.fontSize = '11px';
        tip.style.padding = '6px 8px';
        tip.style.borderRadius = '6px';
        tip.style.whiteSpace = 'nowrap';
        tip.style.display = 'none';
        document.body.appendChild(tip);
        return tip;
    };
    const init = () => {
        const plot = document.querySelector('.js-plotly-plot');
        if (!plot || !plot.on) return false;
        const tip = buildTooltip();
        plot.on('plotly_hover', (ev) => {
            if (!ev || !ev.points || ev.points.length === 0) return;
            const points = ev.points;
            const x = points[0].x;
            const lines = points.map(p => {
                const name = p.data && p.data.name ? p.data.name : '';
                const y = p.y !== undefined && p.y !== null ? Number(p.y).toFixed(2) : '';
                return name ? `${name}-${y}` : `${y}`;
            });
            tip.innerHTML = `<div>${x}</div>${lines.map(l => `<div>${l}</div>`).join('')}`;
            tip.style.display = 'block';
            tip.style.visibility = 'hidden';
            const pageX = ev.event ? ev.event.pageX : 0;
            const pageY = ev.event ? ev.event.pageY : 0;
            const width = tip.offsetWidth;
            const height = tip.offsetHeight;
            const left = pageX - width - 12;
            const top = pageY - height / 2;
            tip.style.left = `${left}px`;
            tip.style.top = `${top}px`;
            tip.style.visibility = 'visible';
        });
        plot.on('plotly_unhover', () => { tip.style.display = 'none'; });
        plot.on('plotly_relayout', () => { tip.style.display = 'none'; });
        return true;
    };
    const start = () => { if (init()) return; setTimeout(start, 50); };
    start();
})();
</script>
"""

def fetch_realtime_sge_fallback():
    url = "https://hq.sinajs.cn/list=shau"
    headers = {'Referer': 'http://finance.sina.com.cn'}
    try:
        response = requests.get(url, headers=headers, timeout=10)
        data_str = re.search(r'"(.*)"', response.text).group(1)
        data_list = data_str.split(',')
        if len(data_list) > 2:
            price = float(data_list[2])
            date = datetime.now(beijing_tz).replace(hour=0, minute=0, second=0, microsecond=0)
            return date, price
    except Exception as e:
        logger.warning(f"备选接口异常: {e}")
    return None, None

# ==========================================
# 数据抓取与页面生成核心
# 采用 Streamlit 缓存机制，正常访问时直接从内存秒出，不消耗 API
# ==========================================
@st.cache_data(ttl=1200, show_spinner=False)
def generate_and_save_html(session="实时"):
    now_beijing = datetime.now(beijing_tz)
    start_date = "2024-01-01"
    future_view = (now_beijing + timedelta(days=7)).strftime('%Y-%m-%d')
    
    try:
        # 1. 铺底历史数据
        df_hist = ak.spot_hist_sge(symbol="Au99.99")
        df_hist['date'] = pd.to_datetime(df_hist['date'])
        df_hist = df_hist[df_hist['date'] >= start_date]
        df_hist = df_hist.rename(columns={'date': 'Date', 'close': 'Gold_RMB_Gram'})
        
        # 2. 获取实时切片数据
        rt_date, rt_price = None, None
        try:
            df_rt = ak.spot_quotations_sge()
            df_rt_au = df_rt[df_rt['品种'] == 'Au99.99']
            if not df_rt_au.empty:
                latest_rt = df_rt_au.iloc[-1]
                rt_price = float(latest_rt['现价'])
                match = re.search(r'(\d{4})年(\d{2})月(\d{2})日', latest_rt['更新时间'])
                rt_date = pd.to_datetime(f"{match.group(1)}-{match.group(2)}-{match.group(3)}") if match else now_beijing.replace(hour=0, minute=0, second=0, microsecond=0)
        except Exception:
            rt_date, rt_price = fetch_realtime_sge_fallback()
        
        # 3. 数据拼接
        if rt_price is not None:
            if not df_hist.empty and df_hist['Date'].iloc[-1].date() == rt_date.date():
                df_hist.iloc[-1, df_hist.columns.get_loc('Gold_RMB_Gram')] = rt_price
            else:
                new_row = pd.DataFrame({'Date': [rt_date], 'Gold_RMB_Gram': [rt_price]})
                df_hist = pd.concat([df_hist, new_row], ignore_index=True)
        
        df = df_hist
        if df.empty: return False
            
        latest_row = df.iloc[-1]
        display_price = latest_row['Gold_RMB_Gram']
        
        # 4. 指标计算
        df['MA5'] = df['Gold_RMB_Gram'].rolling(window=5).mean()
        df['MA10'] = df['Gold_RMB_Gram'].rolling(window=10).mean()
        df['MA20'] = df['Gold_RMB_Gram'].rolling(window=20).mean()
        exp1 = df['Gold_RMB_Gram'].ewm(span=12, adjust=False).mean()
        exp2 = df['Gold_RMB_Gram'].ewm(span=26, adjust=False).mean()
        df['DIF'] = exp1 - exp2
        df['DEA'] = df['DIF'].ewm(span=9, adjust=False).mean()
        df['MACD_Hist'] = (df['DIF'] - df['DEA']) * 2
        
        # 5. 图表绘制
        fig = make_subplots(rows=2, cols=1, shared_xaxes=True, vertical_spacing=0.05, row_heights=[0.7, 0.3])
        
        # --- 终极分离排版：彻底解决标题重叠 ---
        fig.update_layout(
            title={
                'text': f"<b>SGE GOLD REAL-TIME TERMINAL</b><br><span style='font-size:12px; color:#888;'>Seamless History + Real-time Sync | Updated: " + now_beijing.strftime('%Y-%m-%d %H:%M:%S') + "</span>", 
                'x': 0.5, 
                'xanchor': 'center'
            },
            legend=dict(
                orientation="h", 
                yanchor="bottom", 
                y=1.01, 
                xanchor="center", 
                x=0.5, 
                bgcolor='rgba(0,0,0,0)'
            ),
            margin=dict(l=10, r=50, t=150, b=10), # 150px 顶部安全隔离区
            
            paper_bgcolor='#0a0e14', plot_bgcolor='#0a0e14', font=dict(color='#e0e0e0', family='Courier New'),
            hoverlabel=dict(bgcolor="rgba(0,0,0,0)", bordercolor="rgba(0,0,0,0)", align="right", namelength=0, font=dict(size=7, color="rgba(255, 77, 77, 0.6)", family="Courier New")),
            xaxis=dict(showgrid=True, gridcolor='#1f2937', range=[df['Date'].iloc[max(0, len(df)-60)], future_view], type="date"),
            xaxis2=dict(showgrid=True, gridcolor='#1f2937', rangeslider=dict(visible=True, bgcolor='#111827', thickness=0.05), type="date"),
            yaxis=dict(showgrid=True, gridcolor='#1f2937', side='right', title="Price"),
            yaxis2=dict(showgrid=True, gridcolor='#1f2937', side='right', title="MACD"),
            hovermode="closest", autosize=True
        )
        
        fig.update_xaxes(showspikes=True, spikecolor="#555", spikesnap="cursor", spikemode="across")
        fig.update_yaxes(showspikes=True, spikecolor="#555", spikesnap="cursor", spikemode="across")

        fig.add_trace(go.Scatter(x=df['Date'], y=df['Gold_RMB_Gram'], mode='lines', name='Au', line=dict(color='#00ffcc', width=2), fill='tozeroy', fillcolor='rgba(0, 255, 204, 0.05)', hovertemplate="%{x|%Y.%-m.%-d}-Au-%{y:.2f}<extra></extra>", hoverinfo='skip'), row=1, col=1)
        fig.add_trace(go.Scatter(x=df['Date'], y=df['MA5'], mode='lines', name='MA5', line=dict(color='#ff00ff', width=1, dash='dot'), opacity=0.7, hovertemplate="%{x|%Y.%-m.%-d}-MA5-%{y:.2f}<extra></extra>", hoverinfo='skip'), row=1, col=1)
        fig.add_trace(go.Scatter(x=df['Date'], y=df['MA10'], mode='lines', name='MA10', line=dict(color='#00bfff', width=1, dash='dashdot'), opacity=0.7, hovertemplate="%{x|%Y.%-m.%-d}-MA10-%{y:.2f}<extra></extra>", hoverinfo='skip'), row=1, col=1)
        fig.add_trace(go.Scatter(x=df['Date'], y=df['MA20'], mode='lines', name='MA20', line=dict(color='#ffff00', width=1, dash='dash'), opacity=0.7, hovertemplate="%{x|%Y.%-m.%-d}-MA20-%{y:.2f}<extra></extra>", hoverinfo='skip'), row=1, col=1)

        fig.add_annotation(x=latest_row['Date'], y=display_price, text=f"{display_price:.2f}", showarrow=True, arrowhead=2, arrowcolor="#ff4d4d", ax=0, ay=-40, font=dict(size=14, color="#ff4d4d", family="Arial Black"), row=1, col=1)

        fig.add_trace(go.Scatter(x=df['Date'], y=df['DIF'], mode='lines', name='DIF', line=dict(color='#ffffff', width=1), hovertemplate="%{x|%Y.%-m.%-d}-DIF-%{y:.2f}<extra></extra>", hoverinfo='skip'), row=2, col=1)
        fig.add_trace(go.Scatter(x=df['Date'], y=df['DEA'], mode='lines', name='DEA', line=dict(color='#ffff00', width=1), hovertemplate="%{x|%Y.%-m.%-d}-DEA-%{y:.2f}<extra></extra>", hoverinfo='skip'), row=2, col=1)
        colors = ['#ff4d4d' if val >= 0 else '#00ffcc' for val in df['MACD_Hist']]
        fig.add_trace(go.Bar(x=df['Date'], y=df['MACD_Hist'], name='MACD Hist', marker_color=colors, opacity=0.8, hovertemplate="%{x|%Y.%-m.%-d}-MACD-%{y:.2f}<extra></extra>", hoverinfo='skip'), row=2, col=1)

        # 6. 生成 HTML 并注入弹窗
        html_str = fig.to_html(full_html=True, include_plotlyjs='cdn', config={'displayModeBar': True, 'scrollZoom': True})
        injected_html = html_str.replace("<body>", f"<body>{help_modal_html}")
        
        with open("index.html", "w", encoding="utf-8") as f:
            f.write(injected_html)
        return True
    except Exception as e:
        logger.error(f"生成错误: {e}")
        return False

# ==========================================
# 主程序：UI渲染 与 全局高并发物理防刷保护机制
# ==========================================
def main():
    col1, col2 = st.columns([8, 2])
    with col1:
        st.markdown("<h3 style='color:#00ffcc;'>📈 黄金实时量化看板</h3>", unsafe_allow_html=True)
    with col2:
        # 手动强刷逻辑
        if st.button("🔄 同步最新行情", use_container_width=True):
            html_file = "index.html"
            allow_refresh = True
            
            # --- 全局物理级别冷却逻辑 (防万人并发恶刷) ---
            if os.path.exists(html_file):
                # 获取全网最后一次成功生成该文件的时间戳
                file_mtime = os.path.getmtime(html_file)
                current_time = time.time()
                elapsed_seconds = current_time - file_mtime
                
                # 若文件距上次修改不足 1200秒(20分钟)，则拦截全网任何人的强制刷新
                if elapsed_seconds < 1200:
                    allow_refresh = False
                    remaining_mins = int((1200 - elapsed_seconds) / 60) + 1
                    st.warning(f"全网防刷保护中！\n刚刚已有用户触发过更新，请等待 {remaining_mins} 分钟后再试。")
            
            # 通过了物理冷却检测，允许清空缓存并重新抓取
            if allow_refresh:
                with st.spinner('正在从交易所抓取最新切片数据...'):
                    generate_and_save_html.clear()  # 清除 Streamlit 全局缓存
                    success = generate_and_save_html()
                    if success:
                        st.success("全网行情已强制更新！")
                    else:
                        st.error("行情更新失败，请检查接口或网络。")
            
    # 【默认静默加载】：
    # 依靠 @st.cache_data。在不点按钮的情况下，所有人一打开页面就是直接读内存秒回
    generate_and_save_html()
    
    # 渲染图表到前端框架
    if os.path.exists("index.html"):
        with open("index.html", "r", encoding="utf-8") as f:
            html_content = f.read()
            components.html(html_content, height=850, scrolling=True)
    else:
        st.error("⚠️ 图表数据尚未生成，请检查数据接口状态或尝试点击同步。")

if __name__ == "__main__":
    main()
