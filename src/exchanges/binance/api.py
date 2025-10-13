# src/exchanges/binance/api.py

from binance.client import Client
from binance.exceptions import BinanceAPIException
from typing import Optional, Dict, Any, List
from src.utils.logger import get_logger
from ..retry_handler import retry_on_api_error
from ..quantity_calculator import QuantityCalculator


class BinanceClient(QuantityCalculator):
    """Клиент для работы с Binance биржей"""

    def __init__(self, api_key: str, secret: str, position_size: float, leverage: int, symbol: str):
        QuantityCalculator.__init__(self, leverage)

        self.name = "Binance"
        self.api_key = api_key
        self.secret = secret
        self.position_size = position_size
        self.leverage = leverage
        self.logger = get_logger(__name__)

        self.client = Client(
            api_key=self.api_key,
            api_secret=self.secret
        )

        self.initialize_symbol(symbol)

    def initialize_symbol(self, symbol: str):
        """Инициализирует символ: устанавливает плечо и получает параметры"""
        self._setup_leverage(symbol)
        self.get_instrument_info(symbol)
        self.logger.info(f"Актив {symbol} инициализирован: плечо {self.leverage}x")

    def _fetch_instrument_info(self, symbol: str) -> Dict[str, Any]:
        """Получает информацию об инструменте с Binance API"""
        try:
            exchange_info = self.client.futures_exchange_info()

            symbol_info = None
            for s in exchange_info['symbols']:
                if s['symbol'] == symbol:
                    symbol_info = s
                    break

            if not symbol_info:
                raise RuntimeError(f"Символ {symbol} не найден")

            info = {
                'qty_precision': symbol_info.get('quantityPrecision', 3),
                'price_precision': symbol_info.get('pricePrecision', 2),
                'qty_step': None,
                'min_qty': None,
                'tick_size': None
            }

            for filter_item in symbol_info['filters']:
                if filter_item['filterType'] == 'LOT_SIZE':
                    info['qty_step'] = float(filter_item['stepSize'])
                    info['min_qty'] = float(filter_item['minQty'])
                elif filter_item['filterType'] == 'PRICE_FILTER':
                    info['tick_size'] = float(filter_item['tickSize'])

            return info

        except Exception as e:
            self.logger.error(f"Ошибка получения информации об инструменте {symbol}: {e}")
            raise

    def _setup_leverage(self, symbol: str):
        """Устанавливает плечо для символа"""
        try:
            self.client.futures_change_leverage(
                symbol=symbol,
                leverage=self.leverage
            )
        except BinanceAPIException as e:
            if e.code != -4028:
                self.logger.error(f"Ошибка установки плеча для {symbol}: {e}")
        except Exception as e:
            self.logger.error(f"Ошибка установки плеча для {symbol}: {e}")

    @staticmethod
    def supports_fast_reverse() -> bool:
        """Binance поддерживает быстрый разворот через удвоение объема"""
        return True

    @staticmethod
    def normalize_symbol(symbol: str) -> str:
        """
        Нормализует торговый символ для совместимости с биржей

        Args:
            symbol: Торговый символ (например, 'ETHUSDT', 'ETHUSDC.P')

        Returns:
            Нормализованный символ без суффикса .P
        """
        return symbol.rstrip('.P') if symbol.endswith('.P') else symbol

    @staticmethod
    def extract_quote_currency(symbol: str) -> str:
        """
        Извлекает валюту котировки из торгового символа

        Args:
            symbol: Торговый символ (например, 'ETHUSDT', 'ETHUSDC.P')

        Returns:
            Валюта котировки ('USDT', 'USDC')
        """
        clean_symbol = BinanceClient.normalize_symbol(symbol)

        for currency in ('USDT', 'USDC'):
            if clean_symbol.endswith(currency):
                return currency

        return clean_symbol[-4:] if len(clean_symbol) > 4 else clean_symbol

    @retry_on_api_error()
    def get_account_balance(self, currency: str) -> float:
        """Получает баланс аккаунта для указанной валюты"""
        account = self.client.futures_account()

        for asset in account['assets']:
            if asset['asset'] == currency:
                return float(asset['walletBalance'])
        return 0.0

    @retry_on_api_error()
    def get_current_position(self, symbol: str) -> Optional[Dict[str, Any]]:
        """Получает текущую позицию по символу"""
        positions = self.client.futures_position_information(symbol=symbol)

        if positions:
            position = positions[0]
            size = abs(float(position['positionAmt']))

            if size > 0:
                side = "Buy" if float(position['positionAmt']) > 0 else "Sell"
                return {
                    'side': side,
                    'size': size,
                    'entry_price': float(position['entryPrice']),
                    'unrealized_pnl': float(position['unRealizedProfit'])
                }
        return None

    @retry_on_api_error()
    def get_exact_entry_price(self, symbol: str) -> Optional[float]:
        """
        Получает точную цену входа из текущей позиции

        Args:
            symbol: Торговый символ

        Returns:
            Цена входа или None если позиции нет
        """
        try:
            positions = self.client.futures_position_information(symbol=symbol)

            if positions:
                position = positions[0]
                position_amt = float(position['positionAmt'])

                if position_amt != 0:
                    entry_price = float(position['entryPrice'])
                    return entry_price

            return None

        except Exception as e:
            self.logger.error(f"Ошибка получения цены входа для {symbol}: {e}")
            return None

    @retry_on_api_error()
    def get_open_orders(self, symbol: str) -> List[Dict[str, Any]]:
        """
        Получает список открытых ордеров по символу

        Args:
            symbol: Торговый символ

        Returns:
            Список открытых ордеров
        """
        try:
            orders = self.client.futures_get_open_orders(symbol=symbol)
            return orders
        except Exception as e:
            self.logger.error(f"Ошибка получения открытых ордеров для {symbol}: {e}")
            return []

    @retry_on_api_error()
    def place_stop_limit_order(self, symbol: str, side: str, quantity: float,
                               stop_price: float, limit_price: float) -> Optional[Dict[str, Any]]:
        """
        Размещает стоп-лимит ордер с reduceOnly

        Args:
            symbol: Торговый символ
            side: 'BUY' или 'SELL'
            quantity: Количество
            stop_price: Цена активации стопа
            limit_price: Лимитная цена исполнения

        Returns:
            Результат размещения ордера или None при ошибке
        """
        try:
            rounded_quantity = self.round_quantity(quantity, symbol)
            rounded_stop_price = self.round_price(stop_price, symbol)
            rounded_limit_price = self.round_price(limit_price, symbol)

            if not self.validate_quantity(rounded_quantity, symbol):
                return None

            result = self.client.futures_create_order(
                symbol=symbol,
                side=side,
                type='STOP',
                quantity=rounded_quantity,
                price=rounded_limit_price,
                stopPrice=rounded_stop_price,
                timeInForce='GTC',
                reduceOnly=True
            )

            self.logger.info(f"Стоп-лимит ордер успешно размещен. Order ID: {result['orderId']}")
            return result

        except BinanceAPIException as e:
            self.logger.error(f"Binance API ошибка размещения стоп ордера: {e}")
            return None
        except Exception as e:
            self.logger.error(f"Ошибка размещения стоп ордера для {symbol}: {e}")
            return None

    @retry_on_api_error()
    def cancel_stop_order(self, symbol: str, order_id: str) -> bool:
        """
        Отменяет стоп ордер

        Args:
            symbol: Торговый символ
            order_id: ID ордера для отмены

        Returns:
            True если ордер отменен успешно, False иначе
        """
        try:
            self.client.futures_cancel_order(
                symbol=symbol,
                orderId=order_id
            )

            self.logger.info(f"Стоп ордер {order_id} для {symbol} успешно отменен")
            return True

        except BinanceAPIException as e:
            if e.code == -2011:
                self.logger.warning(f"Стоп ордер {order_id} уже не существует или был исполнен")
                return True
            else:
                self.logger.error(f"Binance API ошибка отмены ордера {order_id}: {e}")
                return False
        except Exception as e:
            self.logger.error(f"Ошибка отмены стоп ордера {order_id} для {symbol}: {e}")
            return False

    def open_long_position(self, symbol: str, position_size: float, current_price: float) -> bool:
        """Открывает длинную позицию"""
        return self._open_position(symbol, "BUY", position_size, current_price)

    def open_short_position(self, symbol: str, position_size: float, current_price: float) -> bool:
        """Открывает короткую позицию"""
        return self._open_position(symbol, "SELL", position_size, current_price)

    @retry_on_api_error()
    def _open_position(self, symbol: str, side: str, position_size: float, current_price: float) -> bool:
        """Открывает позицию используя переданную цену из WebSocket"""
        quantity = self.calculate_quantity(symbol, position_size, current_price)

        if not self.validate_quantity(quantity, symbol):
            return False

        self.client.futures_create_order(
            symbol=symbol,
            side=side,
            type='MARKET',
            quantity=quantity
        )

        return True

    @retry_on_api_error()
    def reverse_position_fast(self, symbol: str, total_quantity: float) -> bool:
        """
        Разворот позиции с указанным итоговым объемом

        Args:
            symbol: Торговый символ
            total_quantity: Итоговый объем для разворота (предыдущий + новый)

        Returns:
            True если разворот успешен, False иначе
        """
        current_position = self.get_current_position(symbol)
        if not current_position:
            self.logger.warning(f"Нет позиции для разворота {symbol}")
            return False

        current_side = current_position['side']

        if current_side == "Buy":
            reverse_side = "SELL"
        else:
            reverse_side = "BUY"

        rounded_quantity = self.round_quantity(total_quantity, symbol)

        self.logger.info(
            f"Разворот {symbol}: {current_side} -> {reverse_side}, объем {rounded_quantity}")

        try:
            self.client.futures_create_order(
                symbol=symbol,
                side=reverse_side,
                type='MARKET',
                quantity=rounded_quantity
            )

            new_direction = "Long" if reverse_side == "BUY" else "Short"
            self.logger.info(f"Позиция {symbol} развернута в {new_direction}")
            return True

        except Exception as e:
            self.logger.error(f"Ошибка разворота {symbol}: {e}")
            return False

    @retry_on_api_error()
    def close_position(self, symbol: str) -> bool:
        """Закрывает текущую позицию по символу"""
        position = self.get_current_position(symbol)
        if not position:
            return True

        opposite_side = "SELL" if position['side'] == "Buy" else "BUY"
        rounded_size = self.round_quantity(position['size'], symbol)

        self.client.futures_create_order(
            symbol=symbol,
            side=opposite_side,
            type='MARKET',
            quantity=rounded_size,
            reduceOnly=True
        )

        self.logger.info(f"Закрыта {position['side']} позиция {symbol}, PnL: {position['unrealized_pnl']}")
        return True