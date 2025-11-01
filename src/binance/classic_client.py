# src/binance/classic_client.py

from binance import AsyncClient
from binance.exceptions import BinanceAPIException
from typing import Optional, Dict, Any
from src.utils.logger import get_logger
from src.utils.retry_handler import retry_on_api_error


class ClassicBinanceClient:
    """Async клиент Binance для классической стратегии (без стопов)"""

    def __init__(self, api_key: str, secret: str, position_size: float, leverage: int, symbol: str):
        self.name = "Binance"
        self.api_key = api_key
        self.secret = secret
        self.position_size = position_size
        self.leverage = leverage
        self.symbol = symbol
        self.logger = get_logger(__name__)

        self.client: Optional[AsyncClient] = None
        self._instruments_info: Dict[str, Dict[str, Any]] = {}

    async def initialize(self):
        """Async инициализация клиента"""
        self.client = await AsyncClient.create(
            api_key=self.api_key,
            api_secret=self.secret
        )

        await self._setup_leverage(self.symbol)
        await self._load_instrument_info(self.symbol)
        await self.disable_hedge_mode()

        self.logger.info(f"Актив {self.symbol} инициализирован: плечо {self.leverage}x, ONE-WAY режим")

    async def close(self):
        """Закрытие клиента"""
        if self.client:
            await self.client.close_connection()
            self.logger.info("Binance клиент закрыт")

    async def disable_hedge_mode(self) -> bool:
        """
        Отключает режим хеджирования (переходит в ONE-WAY)

        Returns:
            True если успешно или уже был отключен
        """
        try:
            current_mode = await self.client.futures_get_position_mode()
            is_hedge_enabled = current_mode.get('dualSidePosition', False)

            if not is_hedge_enabled:
                self.logger.info("Режим хеджирования уже отключен (ONE-WAY)")
                return True

            await self.client.futures_change_position_mode(dualSidePosition='false')
            self.logger.info("Режим хеджирования отключен, включен ONE-WAY режим")
            return True

        except BinanceAPIException as e:
            if "No need to change position side" in str(e):
                self.logger.info("Режим хеджирования уже отключен")
                return True
            else:
                self.logger.error(f"Binance API ошибка при отключении хеджирования: {e}")
                return False
        except Exception as e:
            self.logger.error(f"Ошибка отключения режима хеджирования: {e}")
            return False

    async def _setup_leverage(self, symbol: str):
        """Устанавливает плечо для символа"""
        try:
            await self.client.futures_change_leverage(
                symbol=symbol,
                leverage=self.leverage
            )
        except BinanceAPIException as e:
            if e.code != -4028:
                self.logger.error(f"Ошибка установки плеча для {symbol}: {e}")
        except Exception as e:
            self.logger.error(f"Ошибка установки плеча для {symbol}: {e}")

    async def _load_instrument_info(self, symbol: str):
        """Загружает и кеширует информацию об инструменте"""
        try:
            exchange_info = await self.client.futures_exchange_info()

            symbol_info = None
            for s in exchange_info['symbols']:
                if s['symbol'] == symbol:
                    symbol_info = s
                    break

            if not symbol_info:
                raise RuntimeError(f"Символ {symbol} не найден")

            info = {
                'qty_precision': symbol_info.get('quantityPrecision', 3),
                'qty_step': None,
                'min_qty': None
            }

            for filter_item in symbol_info['filters']:
                if filter_item['filterType'] == 'LOT_SIZE':
                    info['qty_step'] = float(filter_item['stepSize'])
                    info['min_qty'] = float(filter_item['minQty'])

            self._instruments_info[symbol] = info

        except Exception as e:
            self.logger.error(f"Ошибка загрузки информации об инструменте {symbol}: {e}")
            raise

    @retry_on_api_error()
    async def get_current_position(self, symbol: str) -> Optional[Dict[str, Any]]:
        """
        Получает текущую открытую позицию по символу

        Args:
            symbol: Торговый символ

        Returns:
            Словарь {side, size, entry_price, unrealized_pnl} или None если позиции нет
        """
        try:
            positions = await self.client.futures_position_information(symbol=symbol)

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

        except Exception as e:
            self.logger.error(f"Ошибка получения текущей позиции для {symbol}: {e}")
            return None

    @retry_on_api_error()
    async def open_long_position(self, symbol: str, quantity: float) -> bool:
        """
        Открывает длинную позицию рыночным ордером

        Args:
            symbol: Торговый символ
            quantity: Количество контрактов (уже округленное)

        Returns:
            True если успешно, False иначе
        """
        try:
            await self.client.futures_create_order(
                symbol=symbol,
                side='BUY',
                type='MARKET',
                quantity=quantity
            )

            self.logger.info(f"LONG позиция открыта: {symbol} x{quantity}")
            return True

        except Exception as e:
            self.logger.error(f"Ошибка открытия LONG позиции {symbol}: {e}")
            return False

    @retry_on_api_error()
    async def open_short_position(self, symbol: str, quantity: float) -> bool:
        """
        Открывает короткую позицию рыночным ордером

        Args:
            symbol: Торговый символ
            quantity: Количество контрактов (уже округленное)

        Returns:
            True если успешно, False иначе
        """
        try:
            await self.client.futures_create_order(
                symbol=symbol,
                side='SELL',
                type='MARKET',
                quantity=quantity
            )

            self.logger.info(f"SHORT позиция открыта: {symbol} x{quantity}")
            return True

        except Exception as e:
            self.logger.error(f"Ошибка открытия SHORT позиции {symbol}: {e}")
            return False

    @retry_on_api_error()
    async def reverse_position_fast(self, symbol: str, total_quantity: float) -> bool:
        """
        Разворот позиции с указанным итоговым объемом

        Args:
            symbol: Торговый символ
            total_quantity: Итоговый объем (уже округленный)

        Returns:
            True если успешен, False иначе
        """
        try:
            current_position = await self.get_current_position(symbol)

            if not current_position:
                self.logger.warning(f"Нет позиции для разворота {symbol}")
                return False

            current_side = current_position['side']
            reverse_side = "SELL" if current_side == "Buy" else "BUY"

            self.logger.info(
                f"Разворот {symbol}: {current_side} → {reverse_side}, объем {total_quantity}"
            )

            await self.client.futures_create_order(
                symbol=symbol,
                side=reverse_side,
                type='MARKET',
                quantity=total_quantity
            )

            new_direction = "LONG" if reverse_side == "BUY" else "SHORT"
            self.logger.info(f"Позиция {symbol} развернута в {new_direction}")
            return True

        except Exception as e:
            self.logger.error(f"Ошибка разворота {symbol}: {e}")
            return False

    def calculate_quantity(self, symbol: str, position_size: float, current_price: float) -> float:
        """
        Вычисляет и округляет количество контрактов для открытия позиции

        Args:
            symbol: Торговый символ
            position_size: Размер позиции в USDT
            current_price: Текущая цена актива

        Returns:
            Количество контрактов (рассчитано и округлено по требованиям биржи)
        """
        total_value = position_size * self.leverage
        raw_quantity = total_value / current_price

        info = self._instruments_info.get(symbol)
        if not info:
            self.logger.warning(f"Информация об инструменте {symbol} не загружена, используем fallback")
            return round(raw_quantity, 3)

        qty_step = info.get('qty_step')
        min_qty = info.get('min_qty')
        qty_precision = info.get('qty_precision', 3)

        if qty_step:
            precision_digits = len(str(qty_step).split('.')[-1]) if '.' in str(qty_step) else 0
            rounded_value = round(raw_quantity / qty_step) * qty_step
            rounded_value = round(rounded_value, precision_digits)
        else:
            rounded_value = round(raw_quantity, qty_precision)

        if min_qty and rounded_value < min_qty:
            rounded_value = min_qty

        return rounded_value

    @staticmethod
    def extract_quote_currency(symbol: str) -> str:
        """
        Извлекает валюту котировки из символа

        Args:
            symbol: Торговый символ (например, 'ETHUSDT', 'ETHUSDC')

        Returns:
            Валюта котировки ('USDT', 'USDC' или последние 4 символа)
        """
        for currency in ('USDT', 'USDC'):
            if symbol.endswith(currency):
                return currency

        return symbol[-4:] if len(symbol) > 4 else symbol