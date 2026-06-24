# QUANTAXIS 2.1.0 完整使用指南

> 量化金融策略框架 — 从数据采集到策略回测的完整工作流

**环境**: Python 3.9–3.12 | MongoDB 6.0+ | Windows/Linux/macOS

---

## 目录

1. [本地开发（不安装）](#1-本地开发不安装到系统)
2. [pip install 使用](#2-pip-install-后使用)
3. [Docker 部署](#3-docker-部署使用)
4. [多市场数据采集](#5-多市场数据采集)
5. [技术指标计算](#6-技术指标计算)
6. [因子研究与分析](#7-因子研究与分析)
7. [自定义策略编写](#8-自定义策略编写)
8. [回测执行与评估](#9-回测执行与评估)
9. [完整工作流示例](#10-完整工作流示例)
10. [QARS2 自定义实现](#11-qars2-自定义实现可行性)

---

## 1. 本地开发（不安装到系统）

### 环境准备

```bash
conda create -n quantaxis python=3.11 -y
conda activate quantaxis
cd D:/Code/trader/QUANTAXIS
pip install -r requirements.txt
```

### 配置环境变量

```bash
# Windows PowerShell
$env:MONGODB_URI = "mongodb://quantaxis:quantaxis@localhost:27017/quantaxis?authSource=admin"
$env:PYTHONIOENCODING = "utf-8"
```

### 设置 Python Path

```bash
# Windows
set PYTHONPATH=D:\Code\trader\QUANTAXIS;%PYTHONPATH%
# Linux/macOS
export PYTHONPATH=$PWD:$PYTHONPATH
```

### 启动数据库

```bash
docker run -d --name quantaxis-mongodb -p 27017:27017 \
  -e MONGO_INITDB_ROOT_USERNAME=quantaxis \
  -e MONGO_INITDB_ROOT_PASSWORD=quantaxis \
  -e MONGO_INITDB_DATABASE=quantaxis mongo:6.0

docker run -d --name quantaxis-clickhouse -p 9000:9000 -p 8123:8123 \
  -e CLICKHOUSE_USER=quantaxis -e CLICKHOUSE_PASSWORD=quantaxis \
  --ulimit nofile=262144:262144 clickhouse:26.3
```

### 直接使用

```python
import QUANTAXIS as QA
QA.QA_SU_save_stock_list('tdx')  # 保存股票列表
```

---

## 2. pip install 后使用

### 安装

```bash
pip install -e .                    # 基础
pip install -e .[performance]       # 含 polars/orjson/msgpack
pip install -e .[rust]              # 含 QARS2 (需额外包)
pip install -e .[full]              # 全部
```

### CLI 命令

```bash
quantaxis       # 交互式 CLI — 所有操作都可以在这里完成
quantaxisq      # TDX 高级数据批量抓取
qarun           # 策略执行器
qawebserver     # Web API 服务 (端口 8010)
```

CLI 常用命令：
```
QA> save stock_list tdx          # 保存股票列表
QA> save stock_day tdx           # 保存全市场日线
QA> save stock_day tdx paralleled=true   # 并行模式
QA> save stock_min tdx           # 保存分钟线
QA> save index_day tdx           # 保存指数日线
QA> save future_day tdx          # 保存期货日线
QA> save future_min tdx          # 保存期货分钟线
```

### 启动全部服务

```bash
# 终端 1: Web API
qawebserver
# 或
python -c "from QUANTAXIS.QAWebServer.server import start_server, handlers; start_server(handlers)"

# 终端 2: Jupyter Lab
jupyter lab --notebook-dir="D:/Code/trader/QUANTAXIS" --port=8889 --no-browser --NotebookApp.token=''
```

### 日常增量更新

```python
from QUANTAXIS.QASU.main import QA_SU_save_stock_day, QA_SU_save_stock_min
QA_SU_save_stock_day('tdx')     # 日线增量（只抓最近几天）
QA_SU_save_stock_min('tdx')     # 分钟线增量
```

---

## 3. Docker 部署使用

### 完整 docker-compose

```bash
cd docker/qa-service-v2.1

# 基础部署（MongoDB + RabbitMQ + Redis + 核心服务）
docker-compose up -d

# 完整部署（含 ClickHouse + 行情采集 + Web 界面）
docker-compose --profile full up -d
```

| 服务 | 端口 | 说明 |
|------|------|------|
| `mongodb` | 27017 | 主数据库 |
| `rabbitmq` | 5672, 15672 | 消息队列 |
| `redis` | 6379 | 缓存 |
| `clickhouse` | 9000, 8123 | 分析库（可选）|
| `quantaxis` | 8010, 8888 | 核心服务 (API + Jupyter) |
| `quantaxis-web` | 8080 | Web 管理界面 |
| `market-collector` | 8011 | 实时行情采集（可选）|

### 单独启动数据库

见上文第 1 节的 docker run 命令，或者参考 `docker/database.md`。

---

## 4. 配置管理

配置文件：`~/.quantaxis/setting/config.ini`

```ini
[MONGODB]
uri = mongodb://quantaxis:quantaxis@localhost:27017/quantaxis?authSource=admin
```

环境变量覆盖：

| 变量 | 说明 | 默认值 |
|------|------|--------|
| `MONGODB_URI` | MongoDB 完整连接串 | `mongodb://localhost:27017` |
| `MONGODB_USER` | 用户名 | (空) |
| `MONGODB_PWD` | 密码 | (空) |
| `MONGODBPORT` | 端口 | 27017 |
| `CLICKHOUSE_IP` | ClickHouse 地址 | 127.0.0.1 |
| `CLICKHOUSE_PORT` | ClickHouse 端口 | 9001 |
| `CLICKHOUSE_USER` | 用户名 | admin |
| `CLICKHOUSE_PASSWORD` | 密码 | admin |
| `QAPUBSUB_IP` | RabbitMQ 地址 | 127.0.0.1 |
| `PYTHONIOENCODING` | 输出编码（Win 建议 utf-8） | 系统默认 |

---

---

# 🚀 完整数据→策略工作流

---

## 5. 多市场数据采集

QUANTAXIS 通过 TDX（通达信）作为主力数据源，支持 A 股、指数、期货、ETF、债券、港股、美股、全球指数、全球期货。

### 5.1 A 股市场

```python
from QUANTAXIS.QASU.main import (
    QA_SU_save_stock_list,         # 股票列表
    QA_SU_save_stock_day,          # 日线
    QA_SU_save_stock_min,          # 分钟线 (1min/5min/15min/30min/60min)
    QA_SU_save_stock_xdxr,         # 除权除息
    QA_SU_save_stock_info,         # 股票基本信息
    QA_SU_save_stock_block,        # 板块信息
    QA_SU_save_stock_transaction,  # 逐笔成交
)

# --- 股票列表 ---
QA_SU_save_stock_list('tdx')

# --- 日线（增量更新） ---
QA_SU_save_stock_day('tdx')

# --- 日线（并行模式，利用多个 TDX IP 加速） ---
QA_SU_save_stock_day('tdx', paralleled=True)

# --- 分钟线 ---
QA_SU_save_stock_min('tdx')

# --- 单只股票 ---
from QUANTAXIS.QASU.main import QA_SU_save_single_stock_day
QA_SU_save_single_stock_day('000001', 'tdx')

# --- 除权除息数据 ---
QA_SU_save_stock_xdxr('tdx')

# --- 逐笔成交 ---
QA_SU_save_stock_transaction('tdx')
```

**直接查询 MongoDB 中已有数据**：

```python
from QUANTAXIS.QAFetch.QAQuery import (
    QA_fetch_stock_list,      # 股票列表 → DataFrame
    QA_fetch_stock_day,       # 日线 → ndarray
    QA_fetch_stock_min,       # 分钟线
    QA_fetch_stock_xdxr,      # 除权除息
    QA_fetch_stock_block,     # 板块
    QA_fetch_stock_transaction, # 逐笔成交
)

# 查询股票列表
stocks = QA_fetch_stock_list()   # 5211 只

# 查询单只股票日线（从 MongoDB）
ds = QA_fetch_stock_day('000001', '2025-06-01', '2025-06-24')
# 返回 DataStruct，包含 .data (numpy array) 和 .open/.close/.high/.low 等属性
```

**高级查询（返回 DataStruct，可直接做指标计算）**：

```python
from QUANTAXIS.QAFetch.QAQuery_Advance import (
    QA_fetch_stock_day_adv,      # 日线 DataStruct
    QA_fetch_stock_min_adv,      # 分钟线 DataStruct
    QA_fetch_stock_day_full_adv, # 某日全市场快照
)

# DataStruct 对象支持 .add_func() 链式调用
data = QA_fetch_stock_day_adv('000001', '2025-01-01', '2025-06-24')
data.add_func(QA.QA_indicator_MA, 5, 10, 20)  # 计算 MA5/10/20
```

### 5.2 指数数据

```python
from QUANTAXIS.QASU.main import (
    QA_SU_save_index_list,
    QA_SU_save_index_day,
    QA_SU_save_index_min,
)

QA_SU_save_index_list('tdx')     # 指数列表
QA_SU_save_index_day('tdx')      # 指数日线（上证、深证、创业板等）
QA_SU_save_index_min('tdx')      # 指数分钟线

# 查询
from QUANTAXIS.QAFetch.QAQuery_Advance import QA_fetch_index_day_adv
sh000001 = QA_fetch_index_day_adv('000001', '2025-01-01', '2025-06-24')  # 上证指数
```

### 5.3 期货数据

```python
from QUANTAXIS.QASU.main import (
    QA_SU_save_future_list,
    QA_SU_save_future_day,
    QA_SU_save_future_day_all,
    QA_SU_save_future_min,
    QA_SU_save_future_min_all,
)

QA_SU_save_future_list('tdx')     # 期货合约列表
QA_SU_save_future_day('tdx')      # 期货日线
QA_SU_save_future_day_all('tdx')  # 所有期货日线
QA_SU_save_future_min('tdx')      # 期货分钟线
QA_SU_save_future_min_all('tdx')  # 所有期货分钟线

# 查询
from QUANTAXIS.QAFetch.QAQuery_Advance import QA_fetch_future_day_adv, QA_fetch_future_min_adv
rb = QA_fetch_future_day_adv('RB2512', '2025-01-01', '2025-06-24')
```

### 5.4 ETF 数据

```python
from QUANTAXIS.QASU.main import (
    QA_SU_save_etf_list,
    QA_SU_save_etf_day,
    QA_SU_save_etf_min,
)

QA_SU_save_etf_list('tdx')       # ETF 列表
QA_SU_save_etf_day('tdx')        # ETF 日线
QA_SU_save_etf_min('tdx')        # ETF 分钟线
```

### 5.5 港股 & 美股

```python
from QUANTAXIS.QASU.save_tdx import (
    QA_SU_save_hkstock_list,
    QA_SU_save_hkstock_day,
    QA_SU_save_hkstock_min,
    QA_SU_save_usstock_list,
    QA_SU_save_usstock_day,
    QA_SU_save_usstock_min,
)

# 港股
QA_SU_save_hkstock_list()        # 港股列表
QA_SU_save_hkstock_day()         # 港股日线
QA_SU_save_hkstock_min()         # 港股分钟线

# 美股
QA_SU_save_usstock_list()        # 美股列表
QA_SU_save_usstock_day()         # 美股日线
QA_SU_save_usstock_min()         # 美股分钟线
```

### 5.6 债券市场

```python
from QUANTAXIS.QAFetch.QATdx import (
    QA_fetch_get_bond_list,
    QA_fetch_get_bond_day,
    QA_fetch_get_bond_min,
)

bonds = QA_fetch_get_bond_list()
bond_data = QA_fetch_get_bond_day('010107', '2025-01-01', '2025-06-24')
```

### 5.7 期权数据

```python
from QUANTAXIS.QASU.main import (
    QA_SU_save_option_contract_list,
    QA_SU_save_option_day_all,
    QA_SU_save_option_min_all,
    QA_SU_save_option_50etf_day,
    QA_SU_save_option_50etf_min,
)

QA_SU_save_option_contract_list('tdx')   # 期权合约列表
QA_SU_save_option_50etf_day('tdx')       # 50ETF 期权日线
QA_SU_save_option_50etf_min('tdx')       # 50ETF 期权分钟线
```

### 5.8 加密货币（多交易所）

比特币、以太坊等，支持 Binance、Huobi、OKEx、Bitfinex、BitMEX：

```python
from QUANTAXIS.QAFetch.QAQuery_Advance import (
    QA_fetch_cryptocurrency_day_adv,
    QA_fetch_cryptocurrency_min_adv,
    QA_fetch_cryptocurrency_list_adv,
)

# BTC 日线
btc = QA_fetch_cryptocurrency_day_adv('binance', 'BTCUSDT', '2025-01-01', '2025-06-24')

# ETH 分钟线
eth = QA_fetch_cryptocurrency_min_adv('binance', 'ETHUSDT', '2025-01-01', '2025-01-07', '5min')
```

支持交易所: `'binance'`, `'huobi'`, `'okex'`, `'bitfinex'`, `'bitmex'`

### 5.9 其他数据源

如果 TDX 不可用，可以用 Tushare、Baostock、EastMoney 作为备用：

```python
# Tushare (需要 token)
from QUANTAXIS.QASU import save_tushare
save_tushare.QA_SU_save_stock_list_to_stock_list()

# Baostock (免费，无需 token)
from QUANTAXIS.QAFetch.QABaostock import QA_fetch_get_stock_day_baostock
df = QA_fetch_get_stock_day_baostock('000001', '2025-01-01', '2025-06-01')

# EastMoney 资金流向
from QUANTAXIS.QAFetch.QAEastMoney import QA_fetch_get_eastmoney_zjlx
zjlx = QA_fetch_get_eastmoney_zjlx('000001')
```

### 5.10 全局市场数据速查表

| 市场 | 获取列表 | 获取日线 | 获取分钟线 | 保存到 MongoDB |
|------|---------|---------|-----------|---------------|
| **A股** | `QA_fetch_get_stock_list()` | `QA_fetch_get_stock_day()` | `QA_fetch_get_stock_min()` | `QA_SU_save_stock_day('tdx')` |
| **指数** | `QA_fetch_get_index_list()` | `QA_fetch_get_index_day()` | `QA_fetch_get_index_min()` | `QA_SU_save_index_day('tdx')` |
| **期货** | `QA_fetch_get_future_list()` | `QA_fetch_get_future_day()`* | `QA_fetch_get_future_min()`* | `QA_SU_save_future_day('tdx')` |
| **ETF** | - | - | - | `QA_SU_save_etf_day('tdx')` |
| **债券** | `QA_fetch_get_bond_list()` | `QA_fetch_get_bond_day()` | `QA_fetch_get_bond_min()` | `QA_SU_save_bond_day()` |
| **港股** | `QA_fetch_get_hkstock_list()` | - | - | `QA_SU_save_hkstock_day()` |
| **美股** | `QA_fetch_get_usstock_list()` | - | - | `QA_SU_save_usstock_day()` |
| **期权** | `QA_fetch_get_option_list()` | - | - | `QA_SU_save_option_day_all('tdx')` |
| **加密货币** | `QA_fetch_cryptocurrency_list_adv()` | `QA_fetch_cryptocurrency_day_adv()` | `QA_fetch_cryptocurrency_min_adv()` | - |

---

## 6. 技术指标计算

QUANTAXIS 内置 36 个 TA 指标，同时兼容 TA-Lib。

### 6.1 内置指标

```python
import QUANTAXIS as QA
from QUANTAXIS.QAFetch.QAQuery_Advance import QA_fetch_stock_day_adv

# 获取数据
data = QA_fetch_stock_day_adv('000001', '2025-01-01', '2025-06-24')

# 用 add_func 链式调用，一次计算多个指标
result = data.add_func(QA.QA_indicator_MA, 5, 10, 20, 60)      # MA 均线
result = data.add_func(QA.QA_indicator_MACD)                     # MACD
result = data.add_func(QA.QA_indicator_BOLL, 20, 2)             # 布林带
result = data.add_func(QA.QA_indicator_RSI, 6, 12, 24)          # RSI
result = data.add_func(QA.QA_indicator_KDJ)                      # KDJ
result = data.add_func(QA.QA_indicator_ATR, 14)                  # 平均真实波幅
```

### 6.2 完整指标列表

| 类别 | 指标 | 函数 |
|------|------|------|
| **趋势** | 移动平均线 | `QA_indicator_MA` |
| | 指数平滑均线 | `QA_indicator_EMA` |
| | MACD | `QA_indicator_MACD` |
| | 趋向指标 DMI | `QA_indicator_DMI` |
| | 瀑布线 PBX | `QA_indicator_PBX` |
| | 平均线差 DMA | `QA_indicator_DMA` |
| | 动量线 MTM | `QA_indicator_MTM` |
| | 佳庆指标 CHO | `QA_indicator_CHO` |
| **超买超卖** | KDJ | `QA_indicator_KDJ` |
| | RSI | `QA_indicator_RSI` |
| | 威廉指标 WR | `QA_indicator_WR` |
| | 乖离率 BIAS | `QA_indicator_BIAS` |
| | CCI 商品通道 | `QA_indicator_CCI` |
| | 变动率 ROC | `QA_indicator_ROC` |
| **量价** | 能量潮 OBV | `QA_indicator_OBV` |
| | 量价趋势 PVT | `QA_indicator_PVT` |
| | 资金流 MFI | `QA_indicator_MFI` |
| **压力支撑** | 布林带 BOLL | `QA_indicator_BOLL` |
| | MIKE 指标 | `QA_indicator_MIKE` |
| | BBI 多空线 | `QA_indicator_BBI` |
| **波动** | ATR 真实波幅 | `QA_indicator_ATR` |
| | 波动率 VSTD | `QA_indicator_VSTD` |
| **其他** | ARBR 人气意愿 | `QA_indicator_ARBR` |
| | CR 能量指标 | `QA_indicator_CR` |
| | VR 成交量变异 | `QA_indicator_VR` |
| | ASI 振动升降 | `QA_indicator_ASI` |
| | 慢速 KDJ | `QA_indicator_SKDJ` |
| | 方向标准差 DDI | `QA_indicator_DDI` |
| | 影线指标 | `QA_indicator_shadow` |

### 6.3 全市场批量计算

```python
from QUANTAXIS.QAFetch.QAQuery_Advance import QA_fetch_stock_day_full_adv

# 获取某日全市场数据，批量计算 MA
all_stocks = QA_fetch_stock_day_full_adv('2025-06-20')
ma_result = all_stocks.add_func(QA.QA_indicator_MA, 20)  # 全市场 MA20
```

### 6.4 TA-Lib 集成（需安装 TA-Lib）

```python
from QUANTAXIS.QAIndicator import talib_qa

# 直接使用 talib 指标
result = talib_qa.QA_indicator_STOCH(data)
result = talib_qa.QA_indicator_ADX(data, 14)
```

---

## 7. 因子研究与分析

因子系统基于 ClickHouse 存储，支持单因子定义 → 计算 → 入库 → 分析 → 回测的完整流程。

### 7.1 自定义因子

```python
from QUANTAXIS.QAFactor.feature import QASingleFactor_DailyBase
from QUANTAXIS.QAFetch.QAClickhouse import QACKClient
from QUANTAXIS.QAIndicator.indicators import QA_indicator_MA
import pandas as pd

class MyMomentumFactor(QASingleFactor_DailyBase):
    """自定义动量因子"""

    def finit(self):
        self.clientr = QACKClient(
            host='localhost', port=9000,
            user='quantaxis', password='quantaxis'
        )
        self.factor_name = 'momentum_20d'

    def calc(self) -> pd.DataFrame:
        """
        计算因子值，返回 DataFrame，必须包含三列:
        ['date', 'code', 'factor']
        """
        # 获取股票列表
        codelist = self.clientr.get_stock_list().order_book_id.tolist()
        start = '2025-01-01'
        end = '2025-06-24'

        # 获取日线数据
        data = self.clientr.get_stock_day_qfq_adv(codelist, start, end)

        # 计算 20 日收益率
        ret = data.closepanel.pct_change(20)

        # 转换为标准因子格式
        res = ret.stack().reset_index()
        res.columns = ['date', 'code', 'factor']
        return res.dropna()
```

### 7.2 因子写入 ClickHouse

```python
# 创建因子实例（自动注册、建表）
factor = MyMomentumFactor()

# 计算并写入 ClickHouse（存储在 factor 数据库）
factor.update_to_database()
print(f'因子 {factor.factor_name} 已注册并入库')
```

### 7.3 因子浏览与查询

```python
from QUANTAXIS.QAFactor.featureView import QAFeatureView

view = QAFeatureView(
    host='localhost', port=9000,
    user='quantaxis', password='quantaxis'
)

# 列出所有因子
print(view.get_all_factorname())      # ['MA10', 'momentum_20d', ...]

# 查询单个因子
factor_data = view.get_single_factor('momentum_20d', '2025-01-01', '2025-06-24')
print(factor_data.head())
# 返回 MultiIndex DataFrame: (date, code) → factor_value

# 查询多个因子
multi = view.get_all_factor_values(
    ['MA10', 'momentum_20d'],
    start='2025-06-01', end='2025-06-24'
)
```

### 7.4 因子分析（用 Alphalens）

```python
from QUANTAXIS.QAFactor.featureAnalysis import QAFeatureAnalysis

# 加载因子数据
factor_data = view.get_single_factor('momentum_20d')

# 创建分析对象
analysis = QAFeatureAnalysis(
    factor_data,
    feature_name='momentum_20d',
    returnday=5  # 5天持仓期
)

# IC 分析
print(f'平均 IC: {analysis.ic.mean():.4f}')
print(f'IR: {analysis.ir.mean():.4f}')

# IC 统计
ic_stats = analysis.ic_statistic()
print(ic_stats)

# 分层回测分析（生成 Alphalens tear sheet）
analysis.create_tear_sheet()

# Rank 分析（因子的截面排名是否稳定）
rank_analysis = analysis.apply_rank()
print(f'Rank IC: {rank_analysis.ic.mean():.4f}')

# 行业中性化分析
# analysis.categorical(data, key='industry')
# analysis.neut(neuted_data, neut_data)
```

### 7.5 因子回测

```python
from QUANTAXIS.QAFactor.featurebacktest import QAFeatureBacktest

# 加载因子
factor = view.get_single_factor('momentum_20d')

# 创建回测
fb = QAFeatureBacktest(
    factor,
    quantile=0.9,       # 买入因子值前 10% 的股票
    init_cash=50000000,   # 初始资金 5000万
    rolling=5             # 每 5 天调仓
)

# 运行回测
fb.run()

# 查看账户
qifi = fb.account.get_qifi()
print(f"期末权益: {qifi['accounts']['balance']:.2f}")
print(f"总收益率: {(qifi['accounts']['balance']/fb.init_cash - 1)*100:.2f}%")
```

---

## 8. 自定义策略编写

### 8.1 策略基类

继承 `QAStrategyCtaBase`，实现回调方法：

```python
from QUANTAXIS.QAStrategy.qactabase import QAStrategyCtaBase
from QUANTAXIS.QAUtil.QAParameter import MARKET_TYPE

class MyStrategy(QAStrategyCtaBase):
    """
    自定义 CTA 策略

    必需参数:
        code: 交易标的代码（单个或列表）
        frequence: 数据频率 ('1min', '5min', '15min', '30min', '60min', 'day')
        start: 回测开始日期
        end: 回测结束日期
        init_cash: 初始资金
    """

    def user_init(self):
        """策略初始化 — 在这里初始化你的变量"""
        self.ma_short = 5
        self.ma_long = 20
        self.pre_ma_short = None
        self.pre_ma_long = None

    def on_bar(self, bar):
        """
        每根 K 线触发一次

        bar 是一个 Series/DataFrame，包含:
            - open, high, low, close, volume
            - 通过 add_func 添加的技术指标列
            - 通过 bar.name 获取 (datetime, code) MultiIndex
        """
        code = bar.name[1] if hasattr(bar.name, '__iter__') else self.get_code()

        # 获取当前价格和技术指标
        close = bar['close']
        ma5 = bar.get('MA5', close)
        ma20 = bar.get('MA20', close)

        # 做多信号: MA5 上穿 MA20
        if (self.pre_ma_short is not None and
            self.pre_ma_short <= self.pre_ma_long and
            ma5 > ma20):
            if self.get_positions(code).volume_long == 0:
                self.send_order(
                    direction='BUY',
                    offset='OPEN',
                    price=close,
                    volume=1,
                    code=code
                )

        # 平多信号: MA5 下穿 MA20
        elif (self.pre_ma_short is not None and
              self.pre_ma_short >= self.pre_ma_long and
              ma5 < ma20):
            if self.get_positions(code).volume_long > 0:
                self.send_order(
                    direction='SELL',
                    offset='CLOSE',
                    price=close,
                    volume=self.get_positions(code).volume_long,
                    code=code
                )

        # 记录上一个值
        self.pre_ma_short = ma5
        self.pre_ma_long = ma20

    def on_tick(self, tick):
        """每笔 Tick 触发（需 Tick 级别数据）"""
        pass

    def on_dailyopen(self):
        """每日开盘触发"""
        pass

    def on_dailyclose(self):
        """每日收盘触发"""
        pass
```

### 8.2 策略中可用的属性和方法

```python
# --- 基本信息 ---
self.code              # 交易标的
self.frequence         # 数据频率
self.market_type       # 市场类型 (FUTURE_CN / STOCK_CN)
self.running_time      # 当前时间
self.bar_id            # 当前 K 线序号

# --- 交易操作 ---
self.send_order(
    direction='BUY',     # BUY / SELL
    offset='OPEN',       # OPEN / CLOSE / CLOSETODAY
    price=10.5,          # 委托价格
    volume=100,          # 数量
    code='000001'        # 代码（默认 self.code）
)

# --- 仓位查询 ---
self.get_positions(code)                # → QA_Position 对象
self.get_positions(code).volume_long    # 多头持仓量
self.get_positions(code).volume_short   # 空头持仓量
self.get_positions(code).open_price_long  # 多头开仓均价
self.get_positions(code).float_profit   # 浮动盈亏

# --- 信号跟踪 ---
self.BarsSinceEntryLong    # 多头持仓 K 线数
self.BarsSinceEntryShort   # 空头持仓 K 线数
self.EntryPriceLong         # 多头开仓价
self.EntryPriceShort        # 空头开仓价

# --- 风控检查 ---
self.check_order(direction, offset, code)  # 检查同方向是否允许开仓

# --- 可视化 ---
self.plot('ma5', ma5_value, 'line')  # 存储临时数据到 self._signal
```

### 8.3 QIFI 账户对象

每个策略内部维护一个 `QIFI_Account` 实例：

```python
self.acc                    # QIFI 账户实例
self.acc.positions          # 持仓字典 {code: QA_Position}
self.acc.get_qifi()         # 导出 QIFI 格式
self.acc.balance            # 当前权益
self.acc.available           # 可用资金
self.acc.settle()           # 结算
```

### 8.4 多标的策略

```python
class MultiAssetStrategy(QAStrategyCtaBase):
    def __init__(self):
        super().__init__(
            code=['000001', '600519', 'IF2512'],  # 多标的
            frequence='day',
            start='2025-01-01',
            end='2025-06-24',
            init_cash=10000000
        )

    def on_bar(self, bar):
        # bar.name = (datetime, code) — 通过 code 区分不同标的
        dt = bar.name[0]
        code = bar.name[1]

        if code == '000001':     # 股票
            self._handle_stock(bar)
        elif code == 'IF2512':   # 期货
            self._handle_future(bar)
```

### 8.5 实盘/模拟盘模式

```python
# 策略通过 RabbitMQ EventMQ 连接实盘数据
strategy = MyStrategy(
    code='rb2005',
    frequence='1min',
    start='2025-01-01',
    end='2025-06-24',
    init_cash=1000000,
    # EventMQ 配置
    data_host='127.0.0.1',
    data_port=5672,
    trade_host='127.0.0.1',
    trade_port=5672,
)

# 模拟盘（需要 RabbitMQ 和行情源）
strategy.run_sim()

# 回测（从 MongoDB 获取历史数据）
strategy.run_backtest()
```

---

## 9. 回测执行与评估

### 9.1 执行回测

```python
import QUANTAXIS as QA

# 创建策略实例
strategy = MyStrategy(
    code='000001',
    frequence='day',
    start='2020-01-01',
    end='2025-06-24',
    init_cash=1000000,
)

# 运行回测
strategy.run_backtest()
```

### 9.2 查看回测结果

```python
# 账户信息
qifi = strategy.acc.get_qifi()
print(f"初始资金:   {strategy.init_cash:,.0f}")
print(f"期末权益:   {qifi['accounts']['balance']:,.2f}")
print(f"可用资金:   {qifi['accounts']['available']:,.2f}")
print(f"平仓盈亏:   {qifi['accounts']['close_profit']:,.2f}")
print(f"持仓盈亏:   {qifi['accounts']['position_profit']:,.2f}")
print(f"浮动盈亏:   {qifi['accounts']['float_profit']:,.2f}")
print(f"手续费:     {qifi['accounts']['commission']:,.2f}")

# 总收益率
total_return = (qifi['accounts']['balance'] / strategy.init_cash - 1) * 100
print(f"总收益率:   {total_return:.2f}%")

# 持仓信息
print(f"当前持仓数: {len(strategy.acc.positions)}")
for code, pos in strategy.acc.positions.items():
    print(f"  {code}: 多 {pos.volume_long} / 空 {pos.volume_short}, "
          f"浮动盈亏 {pos.float_profit:.2f}")
```

### 9.3 保存回测结果到 MongoDB

```python
# 策略有 save 方法
strategy.acc.save()
print(f'已保存到 MongoDB')

# 从 MongoDB 查询历史回测
from QUANTAXIS.QAFetch.QAQuery import QA_fetch_backtest_info, QA_fetch_backtest_history
history = QA_fetch_backtest_history('strategy_id', '2020-01-01', '2025-06-24')
```

### 9.4 Web API 查询

```bash
# 查询 QIFI 账户信息
curl "http://localhost:8010/qifi?account_cookie=QA_STRATEGY"

# 查询多账户管理
curl "http://localhost:8010/qifis?portfolio=default"

# 查询调度状态
curl "http://localhost:8010/scheduler/query"
```

---

## 10. 完整工作流示例

### 10.1 一日 Pipeline

```python
"""
从零开始：数据 → 指标 → 因子 → 策略 → 回测
"""

import QUANTAXIS as QA

# =========================================================================
# Step 1: 采集数据
# =========================================================================
from QUANTAXIS.QASU.main import (
    QA_SU_save_stock_list,
    QA_SU_save_stock_day,
    QA_SU_save_stock_xdxr,
)
from QUANTAXIS.QAFetch.QAQuery_Advance import QA_fetch_stock_day_adv

QA_SU_save_stock_list('tdx')          # 股票列表
QA_SU_save_stock_day('tdx')           # 全市场日线
QA_SU_save_stock_xdxr('tdx')          # 除权除息

# =========================================================================
# Step 2: 加载数据 + 计算技术指标
# =========================================================================
data = QA_fetch_stock_day_adv('000001', '2020-01-01', '2025-06-24')
# 添加指标
data = data.add_func(QA.QA_indicator_MA, 5, 10, 20)
data = data.add_func(QA.QA_indicator_MACD)
data = data.add_func(QA.QA_indicator_BOLL)
data = data.add_func(QA.QA_indicator_RSI, 6, 12, 24)
data = data.add_func(QA.QA_indicator_ATR, 14)

# 此时的 data 是一个 QA_DataStruct_Stock_day 对象
# 包含原始 OHLCV + MA5/MA10/MA20 + MACD 各线 + BOLL 各线 + RSI6/12/24 + ATR14
# 可以用 data.data 查看 ndarray，或用 data.open / data.MA5 访问各列

# =========================================================================
# Step 3: 研究因子（可选，用 ClickHouse）
# =========================================================================
from QUANTAXIS.QAFactor.feature import QASingleFactor_DailyBase
from QUANTAXIS.QAFactor.featurebacktest import QAFeatureBacktest
from QUANTAXIS.QAFactor.featureView import QAFeatureView

# 定义因子
class RSIFactor(QASingleFactor_DailyBase):
    def calc(self):
        # ... 计算 RSI 因子值
        pass

# 因子回测
factor = QAFeatureView().get_single_factor('my_factor')
QAFeatureBacktest(factor, quantile=0.9, init_cash=50000000, rolling=5).run()

# =========================================================================
# Step 4: 编写策略
# =========================================================================
from QUANTAXIS.QAStrategy.qactabase import QAStrategyCtaBase

class DoubleMA(QAStrategyCtaBase):
    def user_init(self):
        self.fast, self.slow = 5, 20
        self.pre_fast, self.pre_slow = 0, 0

    def on_bar(self, bar):
        fast_val = bar.get(f'MA{self.fast}', bar['close'])
        slow_val = bar.get(f'MA{self.slow}', bar['close'])
        code = self.get_code()

        # 金叉买入
        if self.pre_fast <= self.pre_slow and fast_val > slow_val:
            if self.get_positions(code).volume_long == 0:
                self.send_order('BUY', 'OPEN', bar['close'], 1000, code=code)

        # 死叉卖出
        elif self.pre_fast >= self.pre_slow and fast_val < slow_val:
            vol = self.get_positions(code).volume_long
            if vol > 0:
                self.send_order('SELL', 'CLOSE', bar['close'], vol, code=code)

        self.pre_fast, self.pre_slow = fast_val, slow_val

# =========================================================================
# Step 5: 回测
# =========================================================================
strategy = DoubleMA(
    code='000001',
    frequence='day',
    start='2020-01-01',
    end='2025-06-24',
    init_cash=1000000,
)
strategy.run_backtest()

# =========================================================================
# Step 6: 查看结果
# =========================================================================
qifi = strategy.acc.get_qifi()
print(f"期末权益:   {qifi['accounts']['balance']:,.2f}")
print(f"总收益率:   {(qifi['accounts']['balance']/1000000 - 1)*100:.2f}%")
print(f"平仓盈亏:   {qifi['accounts']['close_profit']:,.2f}")
print(f"最大回撤:   需要 QARisk 模块计算")

# 查看持仓
for code, pos in strategy.acc.positions.items():
    print(f"  {code}: 多{pos.volume_long} 盈亏{pos.float_profit:.2f}")
```

### 10.2 多市场组合策略

```python
"""
同时交易股票和期货的组合策略
"""
class ComboStrategy(QAStrategyCtaBase):
    def __init__(self):
        super().__init__(
            code=['000001', 'IF2512', 'RB2512'],
            frequence='day',
            start='2025-01-01',
            end='2025-06-24',
            init_cash=5000000,
        )

    def on_bar(self, bar):
        code = bar.name[1]
        close = bar['close']

        if code == '000001':
            # 股票策略逻辑
            pass
        elif code.startswith('IF'):
            # 股指期货逻辑
            pass
        elif code.startswith('RB'):
            # 螺纹钢逻辑
            pass
```

### 10.3 全市场选股策略

```python
"""
每日盘后扫描全市场，选出符合条件的股票，次日买入
"""
from QUANTAXIS.QAFetch.QAQuery_Advance import (
    QA_fetch_stock_day_full_adv,
    QA_fetch_stock_list_adv,
)

stock_list = QA_fetch_stock_list_adv()

class StockScreener(QAStrategyCtaBase):
    def user_init(self):
        self.pool = []  # 候选池

    def on_dailyclose(self):
        """每日收盘后扫描全市场"""
        self.pool = []
        date = str(self.running_time)[:10]

        # 获取当日全市场数据
        all_data = QA_fetch_stock_day_full_adv(date)

        for code in stock_list.index:
            try:
                close = all_data.data.loc[code]['close']

                # 条件 1: 价格 > MA20
                # 条件 2: 成交量放大
                # ... 更多筛选条件

                self.pool.append(code)
            except:
                pass

    def on_bar(self, bar):
        """次日开盘后按 pool 买入"""
        code = bar.name[1]
        if code in self.pool and self.get_positions(code).volume_long == 0:
            self.send_order('BUY', 'OPEN', bar['open'], 100, code=code)
```

---

## 11. QARS2 自定义实现可行性

### 11.1 QARS2 是什么

QARS2（`qars3` 包）是 QUANTAXIS 的 Rust 高性能核心，通过 PyO3 提供 Python 绑定。当前不开源，但你完全可以自己实现。

### 11.2 核心模块分析

| 模块 | 功能 | 复杂度 | 行数估算 |
|------|------|--------|---------|
| QIFI Account | 账户的 buy/sell/settle | ⭐⭐ 简单 | ~800 行 |
| Position | 持仓管理（多空/今昨分离） | ⭐⭐⭐ 中等 | ~600 行 |
| Order/Trade | 订单队列 + 成交撮合 | ⭐⭐ 简单 | ~400 行 |
| Market Preset | 市场参数（合约乘数/保证金/手续费） | ⭐ 简单 | ~300 行 |
| Backtest Engine | 回测循环 + 数据驱动 | ⭐⭐⭐ 中等 | ~500 行 |
| Settlement | 每日结算逻辑 | ⭐⭐ 简单 | ~200 行 |

### 11.3 实现路线

```
阶段 1: 数据结构 (1-2天)
  ├── 在 Rust 中定义 QIFI 结构体
  │   └── 参考 QUANTAXIS/QARSBridge/QIFI_PROTOCOL.md
  ├── 实现 JSON/serde 序列化
  └── 通过 PyO3 暴露为 Python 类

阶段 2: 账户操作 (2-3天)
  ├── buy / sell — 股票买卖
  ├── buy_open / sell_close — 期货开平
  ├── 保证金计算 — 参考 QAMarket/market_preset.py
  ├── 盈亏计算 — 浮动盈亏 / 平仓盈亏
  └── settle — 每日结算

阶段 3: 回测引擎 (2-3天)
  ├── 数据加载 (从 MongoDB 读日线)
  ├── 按日期迭代 → 调用策略 on_bar
  ├── 订单撮合 → 更新持仓
  └── 返回回测结果

阶段 4: PyO3 绑定 (1天)
  ├── 参考 QARSBridge/qars_account.py 的接口
  ├── 提供 Python 友好 API
  └── 写 setup.py / Cargo.toml 打包
```

### 11.4 关键参考文件

| 文件 | 用途 |
|------|------|
| `QARSBridge/QIFI_PROTOCOL.md` | QIFI 协议规范（含完整 Rust struct 定义） |
| `QIFI/QifiAccount.py` | Python 账户实现参考 |
| `QARSBridge/qars_account.py` | PyO3 包装器方法签名 |
| `QARSBridge/qars_backtest.py` | 回测引擎 PyO3 包装器 |
| `QAMarket/market_preset.py` | 市场参数表（保证金/手续费） |
| `QAMarket/QAPosition.py` | 持仓管理逻辑 |
| `QAMarket/QAOrder.py` | 订单管理逻辑 |

### 11.5 替换方式

实现后只需修改 `QARSBridge/__init__.py`:

```python
# 将 "import qars3" 改为你自己的包名
try:
    import qars_my           # <-- 你的 Rust PyO3 包
    HAS_QARS = True
except ImportError:
    HAS_QARS = False
```

### 11.6 Fallback 保障

即使 Rust 实现崩溃或不完整，现有 Python fallback 保证功能可用：

```python
if has_qars_support():
    account = QARSAccount("test", init_cash=1000000)   # Rust
else:
    from QUANTAXIS.QIFI.QifiAccount import QIFI_Account  # Python
    account = QIFI_Account("test")
```

---

## 附录：项目架构速查

```
QAFetch/      数据源 (TDX/Tushare/Baostock/Binance...)
    ↓
QASU/         数据持久化 (MongoDB + ClickHouse)
    ↓
QAQuery/      数据查询 (返回 DataStruct)
    ↓
QAIndicator/  技术指标 (MA/MACD/BOLL/RSI... 36种)
    ↓
QAFactor/     因子研究 (定义→计算→回测→分析)
    ↓
QAStrategy/   策略框架 (CTA基类 + 多标的)
    ↓
QIFI/         统一账户 (QIFI 协议)
    ↓
QAMarket/     市场预设 + 订单/持仓管理

QAEngine/     异步任务框架
QAPubSub/     RabbitMQ 消息队列
QAWebServer/  Tornado REST API (8010)
QASchedule/   定时任务调度
QARSBridge/   Rust 加速桥接 (可选)
```

---

> 最后更新: 2025-06-24 | QUANTAXIS 2.1.0-alpha2
