# coding: utf-8
# Author: 阿财（Rgveda@github）（11652964@qq.com）
# Created date: 2020-02-27
#
# The MIT License (MIT)
#
# Copyright (c) 2016-2018 yutiansut/QUANTAXIS
#
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in
# all
# copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT.  IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
# SOFTWARE.
"""
OKX api
具体api文档参考: https://www.okx.com/docs-v5/
"""
import requests
import json
import datetime
import time
from dateutil.tz import tzutc
import pandas as pd
import numpy as np
from dateutil.parser import parse
from dateutil.relativedelta import relativedelta
from requests.exceptions import ConnectTimeout, SSLError, ReadTimeout, ConnectionError
from retrying import retry
from urllib.parse import urljoin

from QUANTAXIS.QAUtil.QADate_Adv import (
    QA_util_timestamp_to_str,
    QA_util_datetime_to_Unix_timestamp,
    QA_util_timestamp_to_str,
    QA_util_print_timestamp,
)
from QUANTAXIS.QAUtil import (
    QA_util_log_info,
)

TIMEOUT = 10
ILOVECHINA = "同学！！你知道什么叫做科学上网么？ 如果你不知道的话，那么就加油吧！蓝灯，喵帕斯，VPS，阴阳师，v2ray，随便什么来一个！我翻墙我骄傲！"
# OKX v5 API (已从 okx.com 迁移到 okx.com, v3→v5)
OKX_base_url = "https://www.okx.com/"

# 代理配置：读取环境变量 QA_PROXY 或 HTTPS_PROXY
import os as _os
_proxy_url = _os.environ.get('QA_PROXY') or _os.environ.get('HTTPS_PROXY') or _os.environ.get('https_proxy') or _os.environ.get('HTTP_PROXY') or _os.environ.get('http_proxy') or ''
PROXIES = {'http': _proxy_url, 'https': _proxy_url} if _proxy_url else None

column_names = [
    'time',
    'open',
    'high',
    'low',
    'close',
    'volume',
]

"""
QUANTAXIS 和 okx 的 frequency 常量映射关系 (v5 API bar 参数)
"""
# v5 bar → QA frequency 映射
OKX2QA_FREQUENCY_DICT = {
    "1m": '1min',
    "5m": '5min',
    "15m": '15min',
    "30m": '30min',
    "1H": '60min',
    "1D": 'day',
}
# QA frequency → v5 bar 反向映射
QA2OKX_FREQUENCY_DICT = {v: k for k, v in OKX2QA_FREQUENCY_DICT.items()}
# 兼容旧调用（数字参数）
LEGACY_FREQ_MAP = {
    "60": "1m", "300": "5m", "900": "15m",
    "1800": "30m", "3600": "1H", "86400": "1D",
}
"""
v5 API 单次最多返回 300 bar (原 v3 为 200)
"""
FREQUENCY_SHIFTING = {
    "1m": 18000,     # 300 mins
    "5m": 90000,     # 300*5 mins
    "15m": 270000,
    "30m": 540000,
    "1H": 1080000,
    "1D": 25920000,  # 300 days
}


def format_okx_data_fields(datas, symbol, frequency):
    """
    # 归一化数据字段，转换填充必须字段，删除多余字段
    参数名 	类型 	描述
    time 	String 	开始时间
    open 	String 	开盘价格
    high 	String 	最高价格
    low 	String 	最低价格
    close 	String 	收盘价格
    volume 	String 	交易量
    """
    frame = pd.DataFrame(datas, columns=column_names)
    frame['symbol'] = 'OKX.{}'.format(symbol)
    # v5 API 返回毫秒时间戳字符串 "1730563200000" → 转为秒
    frame['time_stamp'] = frame['time'].astype(np.int64) // 1000
    # UTC时间戳转换为北京时间
    frame['datetime'] = pd.to_datetime(
        frame['time_stamp'], unit='s'
    ).dt.tz_localize('UTC').dt.tz_convert('Asia/Shanghai')
    frame['date'] = frame['datetime'].dt.strftime('%Y-%m-%d')
    frame['date_stamp'] = pd.to_datetime(
        frame['date']
    ).dt.tz_localize('Asia/Shanghai').astype(np.int64) // 10**9
    frame['datetime'] = frame['datetime'].dt.strftime('%Y-%m-%d %H:%M:%S')

    frame['created_at'] = int(
        time.mktime(datetime.datetime.now().utctimetuple())
    )
    frame['updated_at'] = int(
        time.mktime(datetime.datetime.now().utctimetuple())
    )
    frame.drop(['time'], axis=1, inplace=True)
    frame['trade'] = 1
    frame['amount'] = frame.apply(
        lambda x: float(x['volume']) *
        (float(x['open']) + float(x['close'])) / 2,
        axis=1
    )
    if (frequency not in ['1day', 'day', '86400', '1d']):
        frame['type'] = OKX2QA_FREQUENCY_DICT[frequency]
    return frame


@retry(stop_max_attempt_number=3, wait_random_min=50, wait_random_max=100)
def QA_fetch_okx_symbols():
    """
    获取交易币对的列表 (OKX v5 API)
    GET /api/v5/public/instruments?instType=SPOT
    """
    url = urljoin(OKX_base_url, "/api/v5/public/instruments")
    retries = 1
    datas = list()
    while (retries != 0):
        try:
            req = requests.get(url, params={"instType": "SPOT"},
                               timeout=TIMEOUT, proxies=PROXIES)
            retries = 0
        except (ConnectTimeout, ConnectionError, SSLError, ReadTimeout):
            retries = retries + 1
            if (retries % 6 == 0):
                print(ILOVECHINA)
            print("Retry /api/v5/public/instruments #{}".format(retries - 1))
            time.sleep(0.5)

        if (retries == 0):
            resp = json.loads(req.content)
            if resp.get('code') != '0':
                print(f"OKX API error: {resp.get('msg', 'unknown')}")
                return []
            symbol_lists = resp.get('data', [])
            if len(symbol_lists) == 0:
                return []
            for item in symbol_lists:
                # v5 字段名转旧格式兼容 save_okx.py
                datas.append({
                    'symbol': item.get('instId', ''),
                    'base_currency': item.get('baseCcy', ''),
                    'quote_currency': item.get('quoteCcy', ''),
                    'tick_size': item.get('tickSz', ''),
                    'lot_size': item.get('lotSz', ''),
                    'instId': item.get('instId', ''),
                })

    return datas


@retry(stop_max_attempt_number=3, wait_random_min=50, wait_random_max=100)
def QA_fetch_okx_kline_with_auto_retry(
    symbol,
    start_time,
    end_time,
    frequency,
):
    """
    OKX v5 API: GET /api/v5/market/candles
    instId: 产品ID (如 BTC-USDT)
    bar: K线粒度 (1m/5m/15m/30m/1H/1D)
    after: 请求此时间戳之后的数据 (毫秒)
    limit: 单次最多 300 条
    """
    bar = LEGACY_FREQ_MAP.get(str(frequency), frequency)
    url = urljoin(OKX_base_url, "/api/v5/market/candles")
    retries = 1
    while (retries != 0):
        try:
            req = requests.get(
                url,
                proxies=PROXIES,
                params={
                    "instId": symbol,
                    "bar": bar,
                    "after": str(int(start_time * 1000)),
                    "limit": "300",
                },
                timeout=TIMEOUT
            )
            time.sleep(0.5)
            retries = 0
        except (ConnectTimeout, ConnectionError, SSLError, ReadTimeout):
            retries = retries + 1
            if (retries % 6 == 0):
                print(ILOVECHINA)
            print("Retry /api/v5/market/candles #{}".format(retries - 1))
            time.sleep(0.5)

        if (retries == 0):
            resp = json.loads(req.content)
            if resp.get('code') != '0':
                print(f"OKX API error: {resp.get('msg', 'unknown')}")
                return None
            # v5 返回格式: [[ts,o,h,l,c,vol,volCcy,volCcyQuote,confirm], ...]
            # 转为旧格式兼容下游 (只保留前6列: ts,o,h,l,c,vol)
            raw_data = resp.get('data', [])
            converted = []
            for candle in raw_data:
                converted.append([
                    str(candle[0]),   # timestamp string
                    candle[1],         # open
                    candle[2],         # high
                    candle[3],         # low
                    candle[4],         # close
                    candle[5],         # volume
                ])
            # v5 返回降序(新→旧)，时间切片算法期望最新在末尾，需要反转
            converted.reverse()
            return converted

    return None


def QA_fetch_okx_kline(
    symbol,
    start_time,
    end_time,
    frequency,
    callback_func=None
):
    """
    Get the latest symbol's candlestick data
    时间倒序切片获取算法，是各大交易所获取1min数据的神器，因为大部分交易所直接请求跨月跨年的1min分钟数据
    会直接返回空值，只有将 start_epoch，end_epoch 切片细分到 200/300 bar 以内，才能正确返回 kline，
    火币和binance，OKX 均为如此，直接用跨年时间去直接请求上万bar 的 kline 数据永远只返回最近200条数据。
    """
    datas = list()
    reqParams = {}
    reqParams['from'] = end_time - FREQUENCY_SHIFTING[frequency]
    reqParams['to'] = end_time

    while (reqParams['to'] > start_time):
        if ((reqParams['from'] > QA_util_datetime_to_Unix_timestamp())) or \
            ((reqParams['from'] > reqParams['to'])):
            # 出现"未来"时间，一般是默认时区设置，或者时间窗口滚动前移错误造成的
            QA_util_log_info(
                'A unexpected \'Future\' timestamp got, Please check self.missing_data_list_func param \'tzlocalize\' set. More info: {:s}@{:s} at {:s} but current time is {}'
                .format(
                    symbol,
                    frequency,
                    QA_util_print_timestamp(reqParams['from']),
                    QA_util_print_timestamp(
                        QA_util_datetime_to_Unix_timestamp()
                    )
                )
            )
            # 跳到下一个时间段
            reqParams['to'] = int(reqParams['from'] - 1)
            reqParams['from'] = int(reqParams['from'] - FREQUENCY_SHIFTING[frequency])
            continue

        klines = QA_fetch_okx_kline_with_auto_retry(
            symbol,
            reqParams['from'],
            reqParams['to'],
            frequency,
        )
        if (klines is None) or \
            (len(klines) == 0) or \
            ('error' in klines):
            # 出错放弃
            break

        reqParams['to'] = int(reqParams['from'] - 1)
        reqParams['from'] = int(reqParams['from'] - FREQUENCY_SHIFTING[frequency])

        if (klines is None) or \
            ((len(datas) > 0) and (klines[-1][0] == datas[-1][0])):
            # 没有更多数据
            break

        datas.extend(klines)

        if (callback_func is not None):
            frame = format_okx_data_fields(klines, symbol, frequency)
            callback_func(frame, OKX2QA_FREQUENCY_DICT[frequency])

    if len(datas) == 0:
        return None

    # 归一化数据字段，转换填充必须字段，删除多余字段
    frame = format_okx_data_fields(datas, symbol, frequency)
    return frame


def QA_fetch_okx_kline_min(
    symbol,
    start_time,
    end_time,
    frequency,
    callback_func=None
):
    """
    Get the latest symbol's candlestick data with time slices
    时间倒序切片获取算法，是各大交易所获取1min数据的神器，因为大部分交易所直接请求跨月跨年的1min分钟数据
    会直接返回空值，只有将 start_epoch，end_epoch 切片细分到 200/300 bar 以内，才能正确返回 kline，
    火币和binance，OKX 均为如此，用上面那个函数的方式去直接请求上万bar 的分钟 kline 数据是不会有结果的。
    """
    reqParams = {}
    reqParams['from'] = end_time - FREQUENCY_SHIFTING[frequency]
    reqParams['to'] = end_time

    requested_counter = 1
    datas = list()
    while (reqParams['to'] > start_time):
        if ((reqParams['from'] > QA_util_datetime_to_Unix_timestamp())) or \
            ((reqParams['from'] > reqParams['to'])):
            # 出现"未来"时间，一般是默认时区设置，或者时间窗口滚动前移错误造成的
            QA_util_log_info(
                'A unexpected \'Future\' timestamp got, Please check self.missing_data_list_func param \'tzlocalize\' set. More info: {:s}@{:s} at {:s} but current time is {}'
                .format(
                    symbol,
                    frequency,
                    QA_util_print_timestamp(reqParams['from']),
                    QA_util_print_timestamp(
                        QA_util_datetime_to_Unix_timestamp()
                    )
                )
            )
            # 跳到下一个时间段
            reqParams['to'] = int(reqParams['from'] - 1)
            reqParams['from'] = int(reqParams['from'] - FREQUENCY_SHIFTING[frequency])
            continue

        klines = QA_fetch_okx_kline_with_auto_retry(
            symbol,
            reqParams['from'],
            reqParams['to'],
            frequency,
        )
        if (klines is None) or \
            (len(klines) == 0) or \
            ('error' in klines):
            # 出错放弃
            break

        reqParams['to'] = int(reqParams['from'] - 1)
        reqParams['from'] = int(reqParams['from'] - FREQUENCY_SHIFTING[frequency])

        if (callback_func is not None):
            frame = format_okx_data_fields(klines, symbol, frequency)
            callback_func(frame, OKX2QA_FREQUENCY_DICT[frequency])

        if (len(klines) == 0):
            return None


if __name__ == '__main__':
    # url = urljoin(OKX_base_url, "/api/v1/exchangeInfo")
    # print(url)
    # a = requests.get(url)
    # print(a.content)
    # print(json.loads(a.content))
    pass
