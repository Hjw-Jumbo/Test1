import requests
import pandas as pd
import logging
import time
import re
from datetime import datetime
from typing import List, Dict, Optional
from database import get_db_connection

logger = logging.getLogger(__name__)


def _convert_symbol_sina(symbol: str) -> str:
    """Convert symbol to Sina Finance format"""
    if symbol.endswith('.HK'):
        code = symbol.replace('.HK', '')
        return f'rt_hk{code}'
    if symbol.endswith('.SZ'):
        code = symbol.replace('.SZ', '')
        return f'sz{code}'
    if symbol.endswith('.SS'):
        code = symbol.replace('.SS', '')
        return f'sh{code}'
    return symbol


class SinaFinanceClient:
    def __init__(self):
        self._last_request_time = 0
        self._min_interval = 0.3
        self._daily_cache = {}
        self.session = requests.Session()
        self.session.trust_env = False
        self.session.proxies = {}
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36',
            'Referer': 'https://finance.sina.com.cn'
        })

    def _rate_limit(self):
        now = time.time()
        elapsed = now - self._last_request_time
        if elapsed < self._min_interval:
            time.sleep(self._min_interval - elapsed)
        self._last_request_time = time.time()

    def fetch_daily(self, symbol: str, days: int = 365) -> Optional[pd.DataFrame]:
        if symbol in self._daily_cache:
            return self._daily_cache[symbol]
        
        self._rate_limit()
        try:
            if symbol.endswith('.HK'):
                code = symbol.replace('.HK', '')
                url = f'http://web.ifzq.gtimg.cn/appstock/app/hkfqkline/get?param=hk{code},day,,,{days},qfq'
                resp = self.session.get(url, timeout=30)
                resp.raise_for_status()
                data = resp.json()
                if data.get('code') != 0:
                    return None
                stock_data = data.get('data', {}).get(f'hk{code}', {})
                records = stock_data.get('qfqday', [])
                if not records:
                    return None
                df = pd.DataFrame(records, columns=['date', 'open', 'close', 'high', 'low', 'volume', 'extra', 'amplitude', 'turnover'])
                df['date'] = pd.to_datetime(df['date'])
                df = df.set_index('date').sort_index()
                result = pd.DataFrame({
                    'open': df['open'].astype(float),
                    'high': df['high'].astype(float),
                    'low': df['low'].astype(float),
                    'close': df['close'].astype(float),
                    'volume': df['volume'].astype(float)
                })
            elif symbol.endswith('.SZ') or symbol.endswith('.SS'):
                code = symbol.replace('.SZ', '').replace('.SS', '')
                market = 'sh' if symbol.endswith('.SS') else 'sz'
                url = f'https://money.finance.sina.com.cn/quotes_service/api/json_v2.php/CN_MarketData.getKLineData?symbol={market}{code}&scale=240&ma=no&datalen={days}'
                resp = self.session.get(url, timeout=30)
                resp.raise_for_status()
                import json
                data = json.loads(resp.text)
                if not data:
                    return None
                df = pd.DataFrame(data)
                df['day'] = pd.to_datetime(df['day'])
                df = df.set_index('day').sort_index()
                result = pd.DataFrame({
                    'open': df['open'].astype(float),
                    'high': df['high'].astype(float),
                    'low': df['low'].astype(float),
                    'close': df['close'].astype(float),
                    'volume': df['volume'].astype(float)
                })
            else:
                return None
            
            if days:
                result = result.tail(days)
            self._daily_cache[symbol] = result
            return result
        except Exception as e:
            logger.error(f"Sina Finance daily data failed for {symbol}: {e}")
            return None

    def fetch_quote(self, symbol: str) -> Optional[Dict]:
        self._rate_limit()
        try:
            sina_symbol = _convert_symbol_sina(symbol)
            url = f'https://hq.sinajs.cn/list={sina_symbol}'
            headers = {'Referer': 'https://finance.sina.com.cn'}
            resp = self.session.get(url, headers=headers, timeout=10)
            resp.raise_for_status()
            text = resp.text
            if not text or '=' not in text:
                return None
            data_part = text.split('=')[1].strip('";\n')
            if not data_part:
                return None
            parts = data_part.split(',')
            if len(parts) < 10:
                return None
            
            if symbol.endswith('.HK'):
                return {
                    'c': float(parts[6]),
                    'pc': float(parts[3]),
                    'h': float(parts[4]),
                    'l': float(parts[5]),
                    'v': float(parts[12]) if len(parts) > 12 else 0
                }
            else:
                return {
                    'c': float(parts[3]),
                    'pc': float(parts[2]),
                    'h': float(parts[4]),
                    'l': float(parts[5]),
                    'v': float(parts[8])
                }
        except Exception as e:
            logger.error(f"Sina Finance quote failed for {symbol}: {e}")
            return None

    def fetch_overview(self, symbol: str) -> Optional[Dict]:
        df = self.fetch_daily(symbol, days=365)
        if df is None or df.empty:
            return None
        return {
            'symbol': symbol,
            'shortName': symbol,
            'PERatio': None,
            'PriceToBookRatio': None,
            'MarketCap': None,
            'FiftyTwoWeekHigh': float(df['high'].max()),
            'FiftyTwoWeekLow': float(df['low'].min()),
            'AverageDailyVolume10Day': float(df['volume'].tail(10).mean()),
        }

    def fetch_news(self, symbol: str) -> Optional[List[Dict]]:
        self._rate_limit()
        try:
            if symbol.endswith('.SZ') or symbol.endswith('.SS'):
                code = symbol.replace('.SZ', '').replace('.SS', '')
                url = f'https://feed.mix.sina.com.cn/api/roll/get?pageid=153&lid=2516&k=&num=10&page=1'
                resp = self.session.get(url, timeout=10)
                resp.raise_for_status()
                data = resp.json()
                if data and 'result' in data and 'data' in data['result']:
                    news = []
                    for item in data['result']['data'][:10]:
                        news.append({
                            'headline': item.get('title', ''),
                            'summary': item.get('summary', ''),
                            'source': item.get('media_name', ''),
                            'datetime': int(item.get('ctime', 0)),
                            'link': item.get('url', '')
                        })
                    return news
            return None
        except Exception as e:
            logger.error(f"Sina Finance news failed for {symbol}: {e}")
            return None


class DataCollector:
    def __init__(self, config: Dict):
        self.config = config
        self.sina_client = SinaFinanceClient()
        self.db_conn = get_db_connection()

    def fetch_and_store_daily(self, symbol: str) -> bool:
        df = self.sina_client.fetch_daily(symbol)
        if df is None:
            return False
        cursor = self.db_conn.cursor()
        for date, row in df.iterrows():
            cursor.execute('''
                INSERT OR REPLACE INTO stock_daily 
                (symbol, date, open, high, low, close, volume, amount)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ''', (symbol, date.strftime('%Y-%m-%d'), row['open'], row['high'], 
                  row['low'], row['close'], row['volume'], 
                  row.get('amount', row['close'] * row['volume'])))
        self.db_conn.commit()
        logger.info(f"Stored daily data for {symbol}: {len(df)} records")
        return True

    def fetch_and_store_news(self, symbols: List[str], hours: int = 24) -> int:
        count = 0
        for symbol in symbols:
            news = self.sina_client.fetch_news(symbol)
            if news:
                for item in news:
                    publish_time = datetime.fromtimestamp(item.get('datetime', 0))
                    if (datetime.now() - publish_time).total_seconds() < hours * 3600:
                        cursor = self.db_conn.cursor()
                        cursor.execute('''
                            INSERT INTO news (title, source, content, symbols, sentiment, publish_time)
                            VALUES (?, ?, ?, ?, ?, ?)
                        ''', (item.get('headline', ''), item.get('source', ''), 
                              item.get('summary', ''), symbol, 
                              0, publish_time.strftime('%Y-%m-%d %H:%M:%S')))
                        count += 1
            time.sleep(1)
        self.db_conn.commit()
        logger.info(f"Stored {count} news items")
        return count

    def fetch_and_store_macro(self, series_configs: List[Dict]) -> int:
        return 0

    def close(self):
        if self.db_conn:
            self.db_conn.close()
