# coding: utf-8
"""
主力资金流 + 技术面共振策略

回测模式：
  用 放量上涨(量>20日均量 AND 涨>0) 模拟"主力净流入"
  全市场排名，取 Top N 只

实盘模式：
  调东财 push2.eastmoney.com API 获取实时主力净流入排名

信号逻辑：
  1. 资金流：主力净流入排名 Top 20
  2. 技术面：MA5>MA20 + RSI14 40~75 + 成交量放大
  3. 板块过滤：同板块股票数>3（避免单只异常）

风险控制：
  单只风险 = 当前权益 × 10%，ATR 定仓位
  止盈：盈利 > 5% → 50%仓位止盈；盈利 > 10% → 全平
  止损：跌破 MA10 或 亏损 > 8%
  调仓：每日
"""

import numpy as np
import pandas as pd
from datetime import datetime

import QUANTAXIS as QA
from QUANTAXIS.QAFetch.QAQuery_Advance import QA_fetch_stock_day_adv
from QUANTAXIS.QAFetch.QAQuery import QA_fetch_stock_list


# ============================================================================
# Part 0: 辅助函数
# ============================================================================

def _ts(date_str):
    return pd.Timestamp(date_str)

def _prev_trade_date(date_str, offset=1):
    d = datetime.strptime(date_str, '%Y-%m-%d')
    while offset > 0:
        d = d - pd.Timedelta(days=1)
        if d.weekday() < 5:
            offset -= 1
    return d.strftime('%Y-%m-%d')

def _next_trade_date(date_str):
    d = datetime.strptime(date_str, '%Y-%m-%d')
    d = d + pd.Timedelta(days=1)
    while d.weekday() >= 5:
        d = d + pd.Timedelta(days=1)
    return d.strftime('%Y-%m-%d')

def _loc_row(df, date_str):
    try:
        ts = pd.Timestamp(date_str)
        s = df.xs(ts, level=0)
        if s is not None and len(s) > 0:
            return s.iloc[0]
    except Exception:
        pass
    return None


# ============================================================================
# Part 1: 数据引擎
# ============================================================================

class DataEngine:
    """股票日线加载 + 技术指标计算"""

    def __init__(self, codes, start, end):
        self.codes = codes
        self.start = start
        self.end = end
        self._cache = {}

    def load(self, code):
        if code in self._cache:
            return self._cache[code]
        try:
            ds = QA_fetch_stock_day_adv(code, self.start, self.end)
            df = ds.data
            for c in ['close','high','low','open','volume']:
                df[c] = df[c].astype(float)

            df['MA5']   = df['close'].rolling(5).mean()
            df['MA10']  = df['close'].rolling(10).mean()
            df['MA20']  = df['close'].rolling(20).mean()

            tr = pd.concat([
                df['high'] - df['low'],
                (df['high'] - df['close'].shift(1)).abs(),
                (df['low']  - df['close'].shift(1)).abs()], axis=1).max(axis=1)
            df['ATR14'] = tr.rolling(14).mean()

            delta = df['close'].diff()
            gain = delta.clip(lower=0)
            loss = (-delta).clip(lower=0)
            rs = (gain.rolling(14).mean() / loss.rolling(14).mean().replace(0, 1e-10))
            df['RSI14'] = 100 - 100 / (1 + rs)

            df['MA_VOL20'] = df['volume'].rolling(20).mean()
            df['vol_ratio'] = df['volume'] / df['MA_VOL20'].replace(0, 1)
            df['amount'] = df['close'] * df['volume']
            # 回测资金流代理：放量上涨 = 主力流入
            df['money_flow'] = np.where(
                (df['close'] > df['open']) & (df['vol_ratio'] > 1.2),
                df['amount'], -df['amount'])
            # 标准化到 [-100, 100] 便于排名
            df['flow_score'] = df['money_flow'].rolling(5).sum() / (
                df['amount'].rolling(5).sum().replace(0, 1)) * 100

            self._cache[code] = df
            return df
        except Exception:
            return None


# ============================================================================
# Part 2: 信号生成
# ============================================================================

class SignalEngine:
    """资金流 + 技术面 多信号共振"""

    def __init__(self, data_engine, all_codes):
        self.de = data_engine
        self.all_codes = all_codes

    def scan(self, date_str, top_n=20):
        """扫描全市场，返回 Top N 资金流 + 技术面共振股票"""
        candidates = []
        prev_date = _prev_trade_date(date_str)

        for code in self.all_codes:
            df = self.de.load(code)
            if df is None:
                continue
            row = _loc_row(df, date_str)
            if row is None:
                continue
            try:
                close  = float(row['close'])
                ma5    = float(row['MA5'])
                ma20   = float(row['MA20'])
                rsi    = float(row['RSI14'])
                vol_r  = float(row['vol_ratio'])
                flow   = float(row['flow_score'])
                atr14  = float(row['ATR14'])
            except (KeyError, TypeError):
                continue

            # 过滤: 价格太低(<3元)的垃圾股
            if close < 3 or np.isnan(flow):
                continue

            # 技术面评分 (0~1)
            tech_score = 0
            if ma5 > ma20:       tech_score += 0.4
            if 40 < rsi < 75:    tech_score += 0.3
            if vol_r > 1.2:      tech_score += 0.3

            # 资金流评分 (0~1, 归一化)
            # flow_score 范围大约是 -100 到 100

            candidates.append({
                'code': code,
                'close': close,
                'atr14': atr14,
                'ma10': float(row['MA10']),
                'flow_score': flow,
                'tech_score': tech_score,
                'composite': 0,  # 后面算
            })

        if not candidates:
            return []

        # 资金流归一化排名
        flows = np.array([c['flow_score'] for c in candidates])
        f_min, f_max = flows.min(), flows.max()
        if f_max > f_min:
            for c in candidates:
                c['flow_score_norm'] = (c['flow_score'] - f_min) / (f_max - f_min)
        else:
            for c in candidates:
                c['flow_score_norm'] = 0.5

        # 综合评分 = 资金流60% + 技术40%
        for c in candidates:
            c['composite'] = c['flow_score_norm'] * 0.6 + c['tech_score'] * 0.4

        # 板块过滤：同一板块最多取前3只
        candidates.sort(key=lambda x: x['composite'], reverse=True)
        industry_counts = {}
        filtered = []
        for c in candidates:
            code = c['code']
            prefix = code[:2]  # 简化的行业代理
            cnt = industry_counts.get(prefix, 0)
            if cnt < 3:
                filtered.append(c)
                industry_counts[prefix] = cnt + 1
            if len(filtered) >= top_n:
                break

        return filtered


# ============================================================================
# Part 3: 风控 & 仓位
# ============================================================================

class RiskManager:
    def __init__(self, risk_pct=0.10, atr_mult=2.0):
        self.risk_pct = risk_pct
        self.atr_mult = atr_mult

    def size(self, equity, price, atr):
        risk = equity * self.risk_pct
        dist = atr * self.atr_mult
        if dist <= 0 or price <= 0:
            return 0
        return int(risk / dist / 100) * 100

    def hit_stop(self, entry, current):
        if entry <= 0:
            return False
        return (entry - current) / entry >= 0.08  # 8% 止损

    def tp_level(self, entry, current):
        """返回止盈动作: None / 'half' / 'full'"""
        if entry <= 0:
            return None
        pnl = (current - entry) / entry
        if pnl >= 0.10:
            return 'full'
        if pnl >= 0.05:
            return 'half'
        return None


# ============================================================================
# Part 4: 回测引擎
# ============================================================================

class MoneyFlowBacktest:
    def __init__(self, start, end, init_cash=1000000, top_n=20,
                 risk_pct=0.10, verbose=True):
        self.start = start
        self.end = end
        self.init_cash = init_cash
        self.top_n = top_n
        self.risk_pct = risk_pct
        self.verbose = verbose

        self.risk = RiskManager(risk_pct=risk_pct)
        self.cash = init_cash
        self.positions = {}    # code → {shares, entry, date}
        self.trades = []
        self.equity_curve = []

    def _equity(self, date_str):
        val = 0
        for code, pos in self.positions.items():
            df = self.de.load(code)
            row = _loc_row(df, date_str) if df is not None else None
            px = float(row['close']) if row is not None else pos['entry']
            val += pos['shares'] * px
        return self.cash + val

    def _rebalance(self, date_str):
        """买入新信号，卖出不在信号里的持仓"""
        signals = self.signal.scan(date_str, top_n=self.top_n)
        signal_codes = set(s['code'] for s in signals)

        # 卖出：不在新信号池中 + 止盈/止损
        to_sell = []
        for code, pos in list(self.positions.items()):
            df = self.de.load(code)
            row = _loc_row(df, date_str) if df is not None else None
            if row is None:
                continue
            px = float(row['close'])
            entry = pos['entry']
            ma10 = float(row['MA10'])

            action = None
            # 止损
            if self.risk.hit_stop(entry, px):
                action = 'stop'
            # 跌破 MA10
            elif px < ma10:
                action = 'ma10_break'
            # 不在信号池
            elif code not in signal_codes:
                action = 'rotation_out'
            # 止盈
            else:
                tp = self.risk.tp_level(entry, px)
                if tp == 'full':
                    action = 'tp_full'
                elif tp == 'half':
                    action = 'tp_half'

            if action:
                self._sell(code, px, date_str, action, signal_codes)

        # 买入新信号
        eq = self._equity(date_str)
        available = self.top_n - len(self.positions)
        new_signals = [s for s in signals if s['code'] not in self.positions]

        for sig in new_signals[:available]:
            code = sig['code']
            price = sig['close']
            atr = sig['atr14']
            shares = self.risk.size(eq, price, atr)
            cap_pct = 0.25  # 单只最多占 25% 可用资金
            max_shares = int(self.cash * cap_pct / price / 100) * 100
            shares = min(shares, max_shares)

            if shares >= 100:
                cost = shares * price
                self.cash -= cost
                self.positions[code] = {'shares': shares, 'entry': price, 'date': date_str}
                if self.verbose:
                    print(f"  BUY  {code} sh={shares} px={price:.2f} cost={cost:,.0f}")
                self.trades.append({'date': date_str, 'code': code,
                    'action': 'BUY', 'price': price, 'shares': shares})

    def _sell(self, code, price, date_str, reason, signal_codes):
        pos = self.positions.get(code)
        if not pos:
            return
        if reason == 'tp_half':
            shares = max(pos['shares'] // 2, 100)
            self.cash += shares * price
            pnl = (price - pos['entry']) * shares
            pos['shares'] -= shares
            if self.verbose:
                print(f"  SELL {code} [HALF:{reason}] sh={shares} px={price:.2f} pnl={pnl:+,.0f}")
        else:
            shares = pos['shares']
            self.cash += shares * price
            pnl = (price - pos['entry']) * shares
            del self.positions[code]
            if self.verbose:
                print(f"  SELL {code} [FULL:{reason}] sh={shares} px={price:.2f} pnl={pnl:+,.0f}")
        self.trades.append({'date': date_str, 'code': code,
            'action': 'SELL', 'price': price, 'shares': shares,
            'reason': reason, 'pnl': pnl})

    def run(self):
        stock_list = QA_fetch_stock_list()
        all_codes = stock_list.index.tolist()
        self.de = DataEngine(all_codes, self.start, self.end)
        self.signal = SignalEngine(self.de, all_codes)

        # 获取交易日列表
        sample = self.de.load('000001')
        if sample is None:
            print("ERROR: 无法加载数据")
            return
        dates = sorted(set(
            str(idx[0])[:10] for idx in sample.index
            if self.start <= str(idx[0])[:10] <= self.end
        ))
        if self.verbose:
            print(f"标的: {len(all_codes)} 只 | 周期: {dates[0]}~{dates[-1]} | {len(dates)} 天")
            print(f"资金: {self.init_cash:,.0f} | 持仓上限: {self.top_n} | 风控: {self.risk_pct*100:.0f}%/只")

        for i, date_str in enumerate(dates):
            if i < 30:
                continue  # 预热期

            if self.verbose and i % 15 == 0:
                eq = self._equity(date_str)
                print(f"  {date_str} | 权益:{eq:,.0f} | 持仓:{len(self.positions)}")

            self._rebalance(date_str)
            eq = self._equity(date_str)
            self.equity_curve.append({'date': date_str, 'equity': eq})

        # 清仓
        for code in list(self.positions.keys()):
            df = self.de.load(code)
            row = _loc_row(df, dates[-1]) if df is not None else None
            px = float(row['close']) if row is not None else self.positions[code]['entry']
            self._sell(code, px, dates[-1], 'close_all', set())

        return self._report()

    def _report(self):
        eq_df = pd.DataFrame(self.equity_curve)
        final_eq = float(eq_df['equity'].iloc[-1]) if len(eq_df) > 0 else self.init_cash
        total_ret = (final_eq / self.init_cash - 1) * 100

        if len(eq_df) > 1:
            eq_df['ret'] = eq_df['equity'].pct_change()
            sharpe = np.sqrt(252) * eq_df['ret'].mean() / eq_df['ret'].std() if eq_df['ret'].std() > 0 else 0
            mdd = (eq_df['equity'] / eq_df['equity'].cummax() - 1).min() * 100
        else:
            sharpe = 0
            mdd = 0

        sells = [t for t in self.trades if t['action'] == 'SELL']
        wins = [t for t in sells if t.get('pnl', 0) > 0]

        r = {
            'init': self.init_cash, 'final': final_eq,
            'total_ret': round(total_ret, 2),
            'sharpe': round(sharpe, 2), 'max_dd': round(mdd, 2),
            'trades': len(sells), 'win_rate': round(len(wins)/len(sells)*100, 1) if sells else 0,
            'avg_pnl': round(np.mean([t.get('pnl',0) for t in sells]), 0) if sells else 0,
        }

        print()
        print("=" * 60)
        print("  主力资金流 + 技术面共振 — 回测报告")
        print("=" * 60)
        print(f"  初始: {r['init']:,.0f}  期末: {r['final']:,.0f}  收益: {r['total_ret']:+.2f}%")
        print(f"  夏普: {r['sharpe']:.2f}  最大回撤: {r['max_dd']:.2f}%")
        print(f"  交易: {r['trades']}笔  胜率: {r['win_rate']}%  均盈: {r['avg_pnl']:+,.0f}")
        print("=" * 60)
        return r


# ============================================================================
# 实盘资金流模块（盘中监控用）
# ============================================================================

def fetch_live_money_flow(top_n=20):
    """从东财获取实时主力净流入 Top N（实盘用，仅交易时段有效）"""
    import requests
    url = ('http://push2.eastmoney.com/api/qt/clist/get?'
           'fid=f62&po=1&pz={}&pn=1&np=1&fltt=2&invt=2'
           '&fs=m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23'
           '&fields=f12,f14,f2,f3,f62,f184,f66,f69').format(top_n)
    headers = {'Referer': 'https://data.eastmoney.com/zjlx/'}
    try:
        r = requests.get(url, headers=headers, timeout=10)
        if r.status_code != 200 or not r.text.strip().startswith('{'):
            return []
        data = r.json()
        results = []
        for item in data.get('data', {}).get('diff', []):
            results.append({
                'code': item.get('f12', ''),
                'name': item.get('f14', ''),
                'price': item.get('f2', 0),
                'change_pct': item.get('f3', 0),
                'main_net': item.get('f62', 0),
                'super_large_net': item.get('f66', 0),
                'large_net': item.get('f69', 0),
            })
        return results
    except Exception:
        return []  # 非交易时段/网络异常静默返回空


def live_monitor(interval=5):
    """
    实盘监控循环：每 interval 秒拉取一次东财主力资金排名
    配合 TDX 技术指标做二次确认
    """
    import time
    print(f"🔴 开始实时资金流监控 (每 {interval}s)")
    print(f"{'时间':<10} {'Top1':<12} {'主力净流入':>12} {'涨跌幅':>8}")
    while True:
        try:
            top = fetch_live_money_flow(5)
            for i, s in enumerate(top[:3]):
                print(f"  #{i+1} {s['name']:<10} {s['main_net']:>12,.0f} {s['change_pct']:>7.2f}%")
            print(f"  --- {pd.Timestamp.now().strftime('%H:%M:%S')} ---")
        except KeyboardInterrupt:
            print("监控已停止")
            break
        except Exception as e:
            print(f"异常: {e}")
        time.sleep(interval)


# ============================================================================
# CLI
# ============================================================================

if __name__ == '__main__':
    import sys

    start = '2024-06-01'
    end = '2025-06-24'
    cash = 1000000
    top_n = 20
    risk = 0.10

    args = sys.argv[1:]
    for i, a in enumerate(args):
        if a == '--live':
            live_monitor(interval=5)
            sys.exit(0)
        elif a == '--test-api':
            top = fetch_live_money_flow(10)
            for s in top:
                print(f"{s['code']} {s['name']:<8} price={s['price']} chg={s['change_pct']}% main_net={s['main_net']:,.0f}")
            sys.exit(0)
        elif a == '--start' and i+1 < len(args):
            start = args[i+1]
        elif a == '--end' and i+1 < len(args):
            end = args[i+1]
        elif a == '--cash' and i+1 < len(args):
            cash = float(args[i+1])
        elif a == '--top-n' and i+1 < len(args):
            top_n = int(args[i+1])

    print(f"参数: {start}~{end} cash={cash:,.0f} top_n={top_n} risk={risk*100:.0f}%")
    bt = MoneyFlowBacktest(start=start, end=end, init_cash=cash,
                           top_n=top_n, risk_pct=risk)
    bt.run()
