# coding: utf-8
"""
板块轮动策略 — 申万行业趋势识别 + 龙头选股 + 风险控制

四信号共振：
  1. 均线多头：行业成分股 MA5均值 > MA20均值
  2. 动量排名：行业近5日涨幅排名前30%
  3. 成交量放大：行业成分股 vol_ratio>1.5 占比 >30%
  4. RSI 过滤：行业成分股 RSI14均值 40~75（非超卖/极端超买）

策略逻辑：
  1. 行业信号：四信号至少满足3个 → 综合评分排序 → 候选池
  2. 选龙头：行业内成交金额最大
  3. 仓位：单只风险 = 当前权益×10%，ATR×2 止损定仓位
  4. 止盈：触及前高附近出现上影线阻力 → 分批止盈50%
  5. 全平：跌破MA20 或 达到10%止损
  6. 调仓：每日检查 → 次日开盘执行

数据依赖：MongoDB stock_day, stock_list；行业分类映射可替换为真实申万数据
"""

import numpy as np
import pandas as pd
from datetime import datetime, timedelta

import QUANTAXIS as QA
from QUANTAXIS.QAFetch.QAQuery_Advance import QA_fetch_stock_day_adv
from QUANTAXIS.QAFetch.QAQuery import QA_fetch_stock_list


# ============================================================================
# Part 1: 行业分类（申万一级行业 → 股票代码映射）
# 用代表性的行业 ETF/指数成分作为代理
# ============================================================================

# 申万一级行业 → 代表性成分股（精选前20只左右，方便快速回测）
# 完整数据应来自 Tushare sw_daily 或 TDX stock_block，这里是精简样本
SW_INDUSTRY_MAP = {
    '银行':     ['000001','002142','600000','600015','600016','600036',
                 '600919','601009','601128','601166','601169','601229',
                 '601288','601328','601398','601818','601939','601988'],
    '非银金融': ['000166','000712','000728','000776','000783','002423',
                 '002673','002736','002797','300059','600030','600109',
                 '600837','600958','601066','601108','601211','601318',
                 '601336','601377','601456','601688','601878'],
    '食品饮料': ['000568','000596','000799','000858','000860','000869',
                 '000895','002304','002557','002568','002570','600519',
                 '600600','600702','600809','600887','603288','603345',
                 '603369','603589'],
    '医药生物': ['000538','000661','002007','002223','002252','300003',
                 '300015','300122','300142','300347','300529','300601',
                 '300760','600085','600196','600276','600436','600763',
                 '603259','688180'],
    '电子':     ['000725','002049','002241','002371','002415','002475',
                 '300223','300408','300433','301308','600703','600745',
                 '601138','603160','603501','688008','688036','688256',
                 '688981','688396'],
    '计算机':   ['000066','000977','002065','002230','002236','002410',
                 '300033','300059','300339','300674','600570','600588',
                 '600845','601360','688111','688188','688561','688568'],
    '电力设备': ['000400','002129','002202','002459','300014','300124',
                 '300274','300316','300450','300750','600406','600438',
                 '601012','601615','601727','688005','688390','688599'],
    '汽车':     ['000625','000800','002594','300750','301039','600104',
                 '600418','600660','600741','600745','601058','601127',
                 '601238','601633','601689','601799'],
    '家用电器': ['000333','000651','000921','002050','002242','002508',
                 '600690','600839','601956','603486','688169'],
    '房地产':   ['000002','000006','000069','001914','001979','002146',
                 '600048','600325','600383','600606','600848','601155'],
}
DEFAULT_INDUSTRY = '其他'


def get_industry(code):
    """查找股票所属申万行业"""
    for industry, codes in SW_INDUSTRY_MAP.items():
        if code in codes:
            return industry
    return DEFAULT_INDUSTRY


# ============================================================================
# Part 2: 信号计算引擎
# ============================================================================

def _loc_row(df, date_str, default=None):
    """安全地从 MultiIndex (datetime, code) DataFrame 取一行"""
    try:
        ts = pd.Timestamp(date_str)
        sliced = df.xs(ts, level=0)
        if sliced is not None and len(sliced) > 0:
            return sliced.iloc[0]
    except Exception:
        pass
    return default


def _loc_col(df, date_str, col, default=None):
    """安全地从 MultiIndex DataFrame 取某列单个值"""
    row = _loc_row(df, date_str)
    if row is not None and col in row:
        return float(row[col])
    return default


class SectorSignalEngine:
    """行业板块信号计算"""

    def __init__(self, codes, start, end):
        self.codes = codes
        self.start = start
        self.end = end
        self._data_cache = {}

    def load_data(self, code):
        """加载单只股票日线并计算所有指标（绕过 add_func 链式 bug）"""
        if code in self._data_cache:
            return self._data_cache[code]
        try:
            ds = QA_fetch_stock_day_adv(code, self.start, self.end)
            df = ds.data

            # 基础列
            df['close'] = df['close'].astype(float)
            df['high']  = df['high'].astype(float)
            df['low']   = df['low'].astype(float)
            df['volume'] = df['volume'].astype(float)

            # MA5 / MA10 / MA20
            df['MA5']  = df['close'].rolling(5).mean()
            df['MA10'] = df['close'].rolling(10).mean()
            df['MA20'] = df['close'].rolling(20).mean()

            # ATR14
            tr = pd.concat([
                df['high'] - df['low'],
                (df['high'] - df['close'].shift(1)).abs(),
                (df['low']  - df['close'].shift(1)).abs()
            ], axis=1).max(axis=1)
            df['ATR14'] = tr.rolling(14).mean()

            # RSI14
            delta = df['close'].diff()
            gain = delta.clip(lower=0)
            loss = (-delta).clip(lower=0)
            avg_gain = gain.rolling(14).mean()
            avg_loss = loss.rolling(14).mean()
            rs = avg_gain / avg_loss.replace(0, 1e-10)
            df['RSI14'] = 100 - (100 / (1 + rs))

            # 成交量均线
            df['MA_VOL20'] = df['volume'].rolling(20).mean()
            df['vol_ratio'] = df['volume'] / df['MA_VOL20'].replace(0, 1)
            df['amount'] = df['close'] * df['volume']

            self._data_cache[code] = df
            return df
        except Exception:
            return None

    def get_industry_index(self, industry, date_str):
        """构建行业等权指数"""
        codes_in_industry = [c for c in self.codes
                             if get_industry(c) == industry]
        closes = []
        for code in codes_in_industry:
            df = self.load_data(code)
            if df is not None:
                try:
                    row = _loc_row(df, date_str)
                    closes.append(float(row['close']))
                except (KeyError, TypeError):
                    pass
        if len(closes) < 3:
            return None
        return np.mean(closes)

    def calc_industry_momentum(self, industry, date_str, lookback=5):
        """计算行业近N日收益率"""
        idx_t = self.get_industry_index(industry, date_str)
        prev_date = _get_prev_trade_date(date_str, lookback)
        idx_t0 = self.get_industry_index(industry, prev_date) if prev_date else None
        if idx_t and idx_t0 and idx_t0 > 0:
            return (idx_t - idx_t0) / idx_t0
        return 0

    def check_ma_signal(self, industry, date_str):
        """检查行业 MA5 > MA20（取成分股均值）"""
        codes_in_industry = [c for c in self.codes
                             if get_industry(c) == industry]
        ma5_vals, ma20_vals = [], []
        for code in codes_in_industry:
            df = self.load_data(code)
            if df is not None:
                row = _loc_row(df, date_str)
                if row is not None:
                    try:
                        ma5_vals.append(float(row.get('MA5', row['close'])))
                        ma20_vals.append(float(row.get('MA20', row['close'])))
                    except (KeyError, TypeError):
                        pass
        if len(ma5_vals) < 3:
            return False
        return np.mean(ma5_vals) > np.mean(ma20_vals)

    def check_volume_signal(self, industry, date_str):
        """
        检查行业成交量放大：成分股中 vol_ratio > 1.5 的占比 > 30%
        vol_ratio = 当日成交量 / 20日均量
        """
        codes_in_industry = [c for c in self.codes
                             if get_industry(c) == industry]
        expanded = 0
        total = 0
        for code in codes_in_industry:
            df = self.load_data(code)
            if df is not None:
                try:
                    row = _loc_row(df, date_str)
                    if row is not None and float(row.get('vol_ratio', 1)) > 1.5:
                        expanded += 1
                    total += 1
                except (KeyError, TypeError):
                    pass
        return total >= 3 and (expanded / total) >= 0.3

    def check_rsi_signal(self, industry, date_str):
        """
        检查行业 RSI 过滤：
        成分股平均 RSI14 在 40~75 之间（不在超卖区也不在极端超买区）
        """
        codes_in_industry = [c for c in self.codes
                             if get_industry(c) == industry]
        rsi_vals = []
        for code in codes_in_industry:
            df = self.load_data(code)
            if df is not None:
                try:
                    row = _loc_row(df, date_str)
                    if row is not None:
                        rsi_vals.append(float(row.get('RSI14', 50)))
                except (KeyError, TypeError):
                    pass
        if len(rsi_vals) < 3:
            return False
        avg_rsi = np.mean(rsi_vals)
        return 40 < avg_rsi < 75

    def get_industry_signals(self, date_str):
        """返回当日所有行业的信号强度排序 — 四信号共振"""
        industries = list(SW_INDUSTRY_MAP.keys())
        results = []
        momentums = {}
        for ind in industries:
            mom = self.calc_industry_momentum(ind, date_str)
            momentums[ind] = mom
        # 动量排名（前30%）
        ranked = sorted(momentums.items(), key=lambda x: x[1], reverse=True)
        top_n = max(3, int(len(ranked) * 0.3))
        top_industries = set(ind for ind, _ in ranked[:top_n])

        for ind in industries:
            # 四信号共振：均线 + 动量 + 成交量 + RSI
            ma_pass    = self.check_ma_signal(ind, date_str)
            mom_pass   = ind in top_industries
            vol_pass   = self.check_volume_signal(ind, date_str)
            rsi_pass   = self.check_rsi_signal(ind, date_str)

            signal_count = sum([ma_pass, mom_pass, vol_pass, rsi_pass])
            if signal_count >= 3:  # 至少满足3个信号
                # 综合评分：动量权重50% + 成交量放大程度30% + RSI位置20%
                rsi_norm = (60 - abs(self._get_industry_rsi(ind, date_str) - 55)) / 60
                results.append({
                    'industry': ind,
                    'momentum': momentums[ind],
                    'signals': signal_count,
                    'score': (momentums[ind] * 0.5 +
                             (1 if vol_pass else 0) * 0.3 +
                             max(0, rsi_norm) * 0.2),
                })
        return sorted(results, key=lambda x: x['score'], reverse=True)

    def _get_industry_rsi(self, industry, date_str):
        """获取行业平均RSI"""
        codes_in_industry = [c for c in self.codes
                             if get_industry(c) == industry]
        vals = []
        for code in codes_in_industry:
            df = self.load_data(code)
            if df is not None:
                try:
                    vals.append(_loc_col(df, date_str, 'RSI14', 50))
                except (KeyError, TypeError):
                    pass
        return np.mean(vals) if vals else 50

    def pick_leaders(self, industry, date_str, top_n=3):
        """在一个行业内选龙头：成交金额最大的 top_n 只"""
        codes_in_industry = [c for c in self.codes
                             if get_industry(c) == industry]
        candidates = []
        for code in codes_in_industry:
            df = self.load_data(code)
            if df is not None:
                try:
                    row = _loc_row(df, date_str)
                    if row is not None:
                        candidates.append({
                        'code': code,
                        'amount': float(row.get('amount', 0)),
                        'close': float(row['close']),
                        'atr': float(row.get('ATR14', row['close'] * 0.02)),
                        'ma20': float(row.get('MA20', row['close'])),
                    })
                except (KeyError, TypeError):
                    pass
        candidates.sort(key=lambda x: x['amount'], reverse=True)
        return candidates[:top_n]


# ============================================================================
# Part 3: 风险与仓位管理
# ============================================================================

class RiskManager:
    """单只股票风险 R = 当前权益 × 10%"""

    def __init__(self, risk_pct=0.10, atr_mult=2.0):
        self.risk_pct = risk_pct
        self.atr_mult = atr_mult

    def calc_position_size(self, equity, price, atr):
        """ATR 止损定仓位：position = equity * risk% / (ATR * mult)"""
        risk_amount = equity * self.risk_pct
        stop_distance = atr * self.atr_mult
        if stop_distance <= 0 or price <= 0:
            return 0
        shares = int(risk_amount / stop_distance / 100) * 100  # A股100股整数倍
        return max(0, shares)

    def check_stop_loss(self, entry_price, current_price, atr):
        """检查是否触发10%止损"""
        if entry_price <= 0:
            return False
        loss_pct = (entry_price - current_price) / entry_price
        return loss_pct >= self.risk_pct

    def check_take_profit_signal(self, current_price, recent_high, candle):
        """
        检查前高附近 price action 阻力信号
        candle: {'open','high','low','close'}
        条件：高价接近前高（2%内）且上影线 > 2倍实体
        """
        if recent_high <= 0:
            return False
        near_high = abs(current_price - recent_high) / recent_high < 0.02
        body = abs(candle['close'] - candle['open'])
        upper_shadow = candle['high'] - max(candle['close'], candle['open'])
        shadow_signal = body > 0 and (upper_shadow / body) > 2.0
        return near_high and shadow_signal


# ============================================================================
# Part 4: 策略主逻辑
# ============================================================================

def _get_prev_trade_date(date_str, offset=1):
    """简易的上一个交易日（粗略用自然日回溯，实际应从交易日历获取）"""
    d = datetime.strptime(date_str, '%Y-%m-%d')
    for _ in range(offset * 5):
        d = d - timedelta(days=1)
        if d.weekday() < 5:  # 跳过周末
            offset -= 1
            if offset <= 0:
                return d.strftime('%Y-%m-%d')
    return d.strftime('%Y-%m-%d')


def _get_next_trade_date(date_str):
    """简易的下一个交易日"""
    d = datetime.strptime(date_str, '%Y-%m-%d')
    d = d + timedelta(days=1)
    while d.weekday() >= 5:
        d = d + timedelta(days=1)
    return d.strftime('%Y-%m-%d')


class SectorRotationBacktest:
    """板块轮动回测引擎"""

    def __init__(self, start, end, init_cash=1000000, risk_pct=0.10,
                 max_positions=10, verbose=True):
        self.start = start
        self.end = end
        self.init_cash = init_cash
        self.risk_pct = risk_pct
        self.max_positions = max_positions
        self.verbose = verbose

        # 组件
        self.signals = None    # 延迟初始化
        self.risk_mgr = RiskManager(risk_pct=risk_pct)

        # 账户状态
        self.cash = init_cash
        self.positions = {}     # code → {shares, entry_price, entry_date, partial_sold}
        self.trade_log = []
        self.equity_curve = []

    def equity(self, prices):
        """计算当前总权益"""
        position_value = sum(
            pos['shares'] * prices.get(code, pos['entry_price'])
            for code, pos in self.positions.items()
        )
        return self.cash + position_value

    def run(self):
        """主回测循环"""
        stock_list = QA_fetch_stock_list()
        all_codes = stock_list.index.tolist()
        # 过滤只保留有行业归属的
        valid_codes = [c for c in all_codes if get_industry(c) != DEFAULT_INDUSTRY]
        if self.verbose:
            print(f'可用股票: {len(all_codes)}, 有行业归属: {len(valid_codes)}')

        self.signals = SectorSignalEngine(valid_codes, self.start, self.end)

        # 生成交易日列表（从 stock_day 数据中提取）
        sample = self.signals.load_data('000001')
        if sample is None:
            print("ERROR: 无法加载数据，请先运行 save stock_day tdx")
            return
        trade_dates = sorted(set(
            str(idx[0])[:10] for idx in sample.index
            if self.start <= str(idx[0])[:10] <= self.end
        ))

        if self.verbose:
            print(f'交易日范围: {trade_dates[0]} ~ {trade_dates[-1]}, 共 {len(trade_dates)} 天')

        prev_signals = []

        for i, date_str in enumerate(trade_dates):
            if i < 30:  # 前30天用于指标预热
                continue

            if self.verbose and i % 20 == 0:
                eq = self.equity(self._get_snapshot_prices(date_str))
                print(f'  {date_str} | 权益: {eq:,.0f} | 持仓: {len(self.positions)}')

            # --- 卖出检查 ---
            self._check_exits(date_str)

            # --- 信号生成 ---
            industry_signals = self.signals.get_industry_signals(date_str)

            # 过滤已在持仓中的行业
            held_industries = set()
            for code in self.positions:
                held_industries.add(get_industry(code))

            new_signals = [s for s in industry_signals
                           if s['industry'] not in held_industries]

            # --- 买入 ---
            available_slots = self.max_positions - len(self.positions)
            for sig in new_signals[:available_slots]:
                leaders = self.signals.pick_leaders(sig['industry'], date_str, top_n=1)
                if not leaders:
                    continue
                leader = leaders[0]
                code = leader['code']
                price = leader['close']
                atr = leader['atr']
                eq = self.equity(self._get_snapshot_prices(date_str))
                shares = self.risk_mgr.calc_position_size(eq, price, atr)

                if shares >= 100 and shares * price <= self.cash * 0.25:
                    # 单行业最多占可用资金 25%
                    cost = shares * price
                    self.cash -= cost
                    self.positions[code] = {
                        'shares': shares,
                        'entry_price': price,
                        'entry_date': date_str,
                        'partial_sold': False,
                        'industry': sig['industry'],
                    }
                    if self.verbose:
                        print(f'  BUY  {code} {sig["industry"]} '
                              f'shares={shares} price={price:.2f} cost={cost:,.0f}')

            prev_signals = new_signals

            # 记录权益
            eq = self.equity(self._get_snapshot_prices(date_str))
            self.equity_curve.append({'date': date_str, 'equity': eq})

        self._close_all(trade_dates[-1])
        return self.report()

    def _loc_price(self, df, date_str, col='close', default=None):
        """安全地从 MultiIndex (datetime, code) 中取值"""
        try:
            ts = pd.Timestamp(date_str)
            return float(df.xs(ts, level=0).iloc[0][col])
        except Exception:
            return default

    def _get_snapshot_prices(self, date_str):
        """获取当日全市场收盘价快照（取前一日数据，因为当日还未收盘）"""
        prev = _get_prev_trade_date(date_str)
        prices = {}
        for code in list(self.positions.keys()):
            df = self.signals.load_data(code)
            if df is not None:
                p = self._loc_price(df, prev, 'close')
                if p is not None:
                    prices[code] = p
                else:
                    prices[code] = self.positions[code]['entry_price']
        return prices

    def _check_exits(self, date_str):
        """检查卖出条件"""
        ts = pd.Timestamp(date_str)
        to_remove = []
        for code, pos in list(self.positions.items()):
            df = self.signals.load_data(code)
            if df is None:
                continue
            try:
                row = df.xs(ts, level=0).iloc[0]
                price = float(row['close'])
                atr   = float(row['ATR14'])
                ma20  = float(row['MA20'])
                high  = float(row['high'])
                open_p = float(row['open'])
            except (KeyError, IndexError, TypeError):
                continue

            entry_price = pos['entry_price']

            # 条件1: 跌破 MA20 → 全平
            if price < ma20:
                self._sell(code, price, date_str, 'MA20_break', 'full')
                to_remove.append(code)
                continue

            # 条件2: 止损10% → 全平
            if self.risk_mgr.check_stop_loss(entry_price, price, atr):
                self._sell(code, price, date_str, 'stop_loss', 'full')
                to_remove.append(code)
                continue

            # 条件3: 前高阻力 + 上影线 → 止盈50%
            recent_high = max(
                entry_price,
                float(df.loc[:pd.Timestamp(date_str)]['high'].tail(20).max())
            )
            candle = {'open': open_p, 'high': high, 'low': float(row['low']), 'close': price}
            if (not pos.get('partial_sold') and
                    self.risk_mgr.check_take_profit_signal(price, recent_high, candle)):
                self._sell(code, price, date_str, 'take_profit_partial', 'half')
                pos['partial_sold'] = True
                # 更新剩余仓位信息
                remaining = pos['shares'] // 2
                if remaining >= 100:
                    self.positions[code]['shares'] = remaining
                else:
                    to_remove.append(code)

        for code in to_remove:
            if code in self.positions:
                del self.positions[code]

    def _sell(self, code, price, date, reason, scale='full'):
        """执行卖出"""
        pos = self.positions.get(code)
        if not pos:
            return
        if scale == 'half':
            shares = pos['shares'] // 2
        else:
            shares = pos['shares']

        if shares < 100:
            return

        revenue = shares * price
        self.cash += revenue
        pnl = (price - pos['entry_price']) * shares
        pnl_pct = (price / pos['entry_price'] - 1) * 100

        if self.verbose:
            print(f'  SELL {code} [{reason}] shares={shares} '
                  f'price={price:.2f} pnl={pnl:+,.0f} ({pnl_pct:+.1f}%)')

        self.trade_log.append({
            'date': date, 'code': code, 'action': 'SELL',
            'reason': reason, 'shares': shares,
            'price': price, 'pnl': pnl, 'pnl_pct': pnl_pct,
        })

    def _close_all(self, date_str):
        """清仓所有持仓"""
        for code in list(self.positions.keys()):
            df = self.signals.load_data(code)
            if df is not None:
                try:
                    price = _loc_col(df, date_str, 'close', pos['entry_price'])
                except (KeyError, TypeError):
                    price = self.positions[code]['entry_price']
            else:
                price = self.positions[code]['entry_price']
            self._sell(code, price, date_str, 'close_all', 'full')
        self.positions.clear()

    def report(self):
        """输出回测报告"""
        total_return = (self.equity(self._get_snapshot_prices(
            self.equity_curve[-1]['date'])) / self.init_cash - 1) * 100

        buys = [t for t in self.trade_log if t.get('action') == 'BUY']
        sells = [t for t in self.trade_log if t.get('action') == 'SELL']
        win_trades = [t for t in sells if t.get('pnl', 0) > 0]

        equity_df = pd.DataFrame(self.equity_curve)
        if len(equity_df) > 1:
            equity_df['ret'] = equity_df['equity'].pct_change()
            sharpe = np.sqrt(252) * equity_df['ret'].mean() / equity_df['ret'].std() \
                if equity_df['ret'].std() > 0 else 0
            max_dd = (equity_df['equity'] / equity_df['equity'].cummax() - 1).min() * 100
        else:
            sharpe = 0
            max_dd = 0

        report = {
            'start': self.start,
            'end': self.end,
            'init_cash': self.init_cash,
            'final_equity': float(equity_df['equity'].iloc[-1]) if len(equity_df) > 0 else self.init_cash,
            'total_return_pct': round(total_return, 2),
            'sharpe': round(sharpe, 2),
            'max_drawdown_pct': round(max_dd, 2),
            'total_trades': len(sells),
            'win_rate': round(len(win_trades) / len(sells) * 100, 1) if sells else 0,
            'avg_pnl_pct': round(np.mean([t.get('pnl_pct', 0) for t in sells]), 2) if sells else 0,
        }

        print()
        print('=' * 60)
        print('  板块轮动策略 — 回测报告')
        print('=' * 60)
        print(f'  回测区间:   {report["start"]} ~ {report["end"]}')
        print(f'  初始资金:   {report["init_cash"]:,.0f}')
        print(f'  期末权益:   {report["final_equity"]:,.0f}')
        print(f'  总收益率:   {report["total_return_pct"]:+.2f}%')
        print(f'  夏普比率:   {report["sharpe"]:.2f}')
        print(f'  最大回撤:   {report["max_drawdown_pct"]:.2f}%')
        print(f'  交易次数:   {report["total_trades"]}')
        print(f'  胜率:       {report["win_rate"]}%')
        print(f'  平均盈亏:   {report["avg_pnl_pct"]:+.2f}%')
        print('=' * 60)

        return report


# ============================================================================
# CLI 入口
# ============================================================================

if __name__ == '__main__':
    import sys

    # 默认参数
    start = '2024-06-01'
    end = '2025-06-24'
    init_cash = 1000000
    risk_pct = 0.10
    max_pos = 10

    # 可通过命令行参数修改
    for i, arg in enumerate(sys.argv[1:]):
        if arg == '--start' and i + 1 < len(sys.argv) - 1:
            start = sys.argv[i + 2]
        elif arg == '--end' and i + 1 < len(sys.argv) - 1:
            end = sys.argv[i + 2]
        elif arg == '--cash' and i + 1 < len(sys.argv) - 1:
            init_cash = float(sys.argv[i + 2])
        elif arg == '--risk' and i + 1 < len(sys.argv) - 1:
            risk_pct = float(sys.argv[i + 2])
        elif arg == '--max-pos' and i + 1 < len(sys.argv) - 1:
            max_pos = int(sys.argv[i + 2])

    print(f'策略参数: start={start} end={end} cash={init_cash:,.0f} risk={risk_pct*100:.0f}% max_pos={max_pos}')
    bt = SectorRotationBacktest(
        start=start, end=end,
        init_cash=init_cash,
        risk_pct=risk_pct,
        max_positions=max_pos,
    )
    bt.run()
