# src/binance/api.py

from binance import AsyncClient
from binance.exceptions import BinanceAPIException
from typing import Optional, Dict, Any, List
from src.utils.logger import get_logger
from src.utils.retry_handler import retry_on_api_error


class BinanceClient:
    """Async клиент для работы с Binance биржей"""

    def __init__(self, api_key: str, secret: str, position_size: float, leverage: int, symbol: str):
        self.name = "Binance"
        self.api_key = api_key
        self.secret = secret
        self.position_size = position_size
        self.leverage = leverage
        self.logger = get_logger(__name__)

        self.client: Optional[AsyncClient] = None
        self.symbol = symbol
        self._instruments_info: Dict[str, Dict[str, Any]] = {}

    async def initialize(self):
        """Async инициализация клиента и символа"""
        self.client = await AsyncClient.create(
            api_key=self.api_key,
            api_secret=self.secret
        )

        await self._setup_leverage(self.symbol)
        await self.get_instrument_info(self.symbol)
        self.logger.info(f"Актив {self.symbol} инициализирован: плечо {self.leverage}x")

    async def close(self):
        """Закрытие клиента"""
        if self.client:
            await self.client.close_connection()
            self.logger.info("Binance клиент закрыт")

    async def get_instrument_info(self, symbol: str) -> Dict[str, Any]:
        """Получает информацию об инструменте с кешированием"""
        if symbol in self._instruments_info:
            return self._instruments_info[symbol]

        info = await self._fetch_instrument_info(symbol)
        self._instruments_info[symbol] = info

        return info

    async def _fetch_instrument_info(self, symbol: str) -> Dict[str, Any]:
        """Получает информацию об инструменте с Binance API"""
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

    @retry_on_api_error()
    async def enable_hedge_mode(self) -> bool:
        """
        Включает режим хеджирования если он отключен

        Returns:
            True если режим включен или уже был включен
        """
        try:
            current_mode = await self.client.futures_get_position_mode()
            is_hedge_enabled = current_mode.get('dualSidePosition', False)

            if is_hedge_enabled:
                self.logger.info(f"Режим хеджирования уже включен")
                return True

            await self.client.futures_change_position_mode(dualSidePosition='true')
            self.logger.info(f"Режим хеджирования успешно включен")
            return True

        except BinanceAPIException as e:
            if "No need to change position side" in str(e):
                self.logger.info(f"Режим хеджирования уже включен")
                return True
            elif "Modify Hedge mode is not allowed" in str(e):
                self.logger.error("Нельзя сменить режим — есть открытые позиции или ордера")
                return False
            else:
                self.logger.error(f"Binance API ошибка: {e}")
                return False
        except Exception as e:
            self.logger.error(f"Ошибка включения режима хеджирования: {e}")
            return False

    @retry_on_api_error()
    async def open_position_with_sl(self, symbol: str, side: str, quantity: float,
                                    stop_price: float, position_side: str = 'LONG') -> Optional[Dict[str, Any]]:
        """
        Открывает позицию рыночным ордером со встроенным стоп лоссом

        Args:
            symbol: Торговый символ
            side: 'BUY' или 'SELL'
            quantity: Количество
            stop_price: Цена активации стопа
            position_side: 'LONG' или 'SHORT'

        Returns:
            Результат размещения основного ордера или None при ошибке
        """
        try:
            rounded_quantity = self.round_quantity(quantity, symbol)
            rounded_stop_price = self.round_price(stop_price, symbol)

            if not self.validate_quantity(rounded_quantity, symbol):
                return None

            sl_side = 'SELL' if side == 'BUY' else 'BUY'

            main_order = await self.client.futures_create_order(
                symbol=symbol,
                side=side,
                type='MARKET',
                quantity=rounded_quantity,
                positionSide=position_side
            )

            self.logger.info(f"Ордер открыт: {main_order['orderId']}")

            sl_order = await self.client.futures_create_order(
                symbol=symbol,
                side=sl_side,
                type='STOP_MARKET',
                stopPrice=rounded_stop_price,
                positionSide=position_side,
                closePosition=True,
                timeInForce='GTE_GTC',
                workingType='MARK_PRICE',
                priceProtect=True
            )

            self.logger.info(f"SL установлен на {rounded_stop_price}: {sl_order['orderId']}")
            return main_order

        except BinanceAPIException as e:
            self.logger.error(f"Binance API ошибка открытия позиции со SL: {e}")
            return None
        except Exception as e:
            self.logger.error(f"Ошибка открытия позиции со SL для {symbol}: {e}")
            return None

    @retry_on_api_error()
    async def update_stop_loss(self, symbol: str, new_stop_price: float,
                               position_side: str = 'LONG') -> bool:
        """
        Отменяет старый стоп лосс и выставляет новый

        Args:
            symbol: Торговый символ
            new_stop_price: Новая цена стопа
            position_side: 'LONG' или 'SHORT'

        Returns:
            True если стоп успешно обновлен
        """
        try:
            sl_side = 'SELL' if position_side == 'LONG' else 'BUY'
            rounded_stop_price = self.round_price(new_stop_price, symbol)

            open_orders = await self.client.futures_get_open_orders(symbol=symbol)

            old_sl_found = False
            for order in open_orders:
                if (order['type'] == 'STOP_MARKET' and
                        order['side'] == sl_side and
                        order['positionSide'] == position_side):

                    await self.client.futures_cancel_order(
                        symbol=symbol,
                        orderId=order['orderId']
                    )
                    self.logger.info(f"Старый SL отменён: {order['stopPrice']}")
                    old_sl_found = True
                    break

            if not old_sl_found:
                self.logger.warning(f"Старый SL не найден для {symbol} {position_side}")
                return False

            new_sl = await self.client.futures_create_order(
                symbol=symbol,
                side=sl_side,
                type='STOP_MARKET',
                stopPrice=rounded_stop_price,
                positionSide=position_side,
                closePosition=True,
                timeInForce='GTE_GTC',
                workingType='MARK_PRICE',
                priceProtect=True
            )

            self.logger.info(f"Новый SL установлен на {rounded_stop_price}: {new_sl['orderId']}")
            return True

        except BinanceAPIException as e:
            self.logger.error(f"Binance API ошибка обновления SL: {e}")
            return False
        except Exception as e:
            self.logger.error(f"Ошибка обновления SL для {symbol}: {e}")
            return False

    @retry_on_api_error()
    async def cancel_all_hedge_stops(self, symbol: str, position_side: str = 'LONG') -> bool:
        """
        Отменяет все стоп ордера для позиции хеджирования

        Args:
            symbol: Торговый символ
            position_side: 'LONG' или 'SHORT'

        Returns:
            True если все стопы отменены успешно
        """
        try:
            open_orders = await self.client.futures_get_open_orders(symbol=symbol)

            sl_side = 'SELL' if position_side == 'LONG' else 'BUY'
            cancelled_count = 0

            for order in open_orders:
                if (order['type'] == 'STOP_MARKET' and
                        order['side'] == sl_side and
                        order['positionSide'] == position_side):

                    await self.client.futures_cancel_order(
                        symbol=symbol,
                        orderId=order['orderId']
                    )
                    self.logger.info(f"Отменён стоп ордер {order['orderId']} на {order['stopPrice']}")
                    cancelled_count += 1

            if cancelled_count > 0:
                self.logger.info(f"Отменено {cancelled_count} стоп ордеров для {symbol} {position_side}")
                return True
            else:
                self.logger.warning(f"Стоп ордеров для {symbol} {position_side} не найдено")
                return True

        except BinanceAPIException as e:
            self.logger.error(f"Binance API ошибка отмены стопов: {e}")
            return False
        except Exception as e:
            self.logger.error(f"Ошибка отмены стопов для {symbol}: {e}")
            return False

    def calculate_quantity(self, symbol: str, position_size: float, current_price: float) -> float:
        """
        Вычисляет и округляет количество для торговли

        Args:
            symbol: Торговый символ
            position_size: Размер позиции в валюте котировки
            current_price: Текущая цена

        Returns:
            Округленное количество для торговли
        """
        total_value = position_size * self.leverage
        raw_quantity = total_value / current_price
        rounded_quantity = self.round_quantity(raw_quantity, symbol)

        return rounded_quantity

    def _round_value(self, value: float, symbol: str, value_type: str) -> float:
        """
        Универсальное округление для цены или количества

        Args:
            value: Значение для округления
            symbol: Торговый символ
            value_type: 'quantity' или 'price'

        Returns:
            Округленное значение
        """
        info = self._instruments_info.get(symbol)
        if not info:
            self.logger.warning(f"Информация об инструменте {symbol} не загружена, используем fallback")
            return round(value, 3 if value_type == 'quantity' else 2)

        if value_type == 'quantity':
            step_key = 'qty_step'
            precision_key = 'qty_precision'
            fallback_precision = 3
        else:
            step_key = 'tick_size'
            precision_key = 'price_precision'
            fallback_precision = 2

        step = info.get(step_key)
        precision = info.get(precision_key)

        if step:
            precision_digits = len(str(step).split('.')[-1]) if '.' in str(step) else 0
            rounded_value = round(value / step) * step
            rounded_value = round(rounded_value, precision_digits)
        elif precision is not None:
            rounded_value = round(value, precision)
        else:
            rounded_value = round(value, fallback_precision)

        if value_type == 'quantity':
            min_qty = info.get('min_qty')
            if min_qty and rounded_value < min_qty:
                rounded_value = min_qty

        return rounded_value

    def round_quantity(self, quantity: float, symbol: str) -> float:
        """
        Округляет количество в соответствии с требованиями биржи

        Args:
            quantity: Исходное количество
            symbol: Торговый символ

        Returns:
            Округленное количество
        """
        return self._round_value(quantity, symbol, 'quantity')

    def round_price(self, price: float, symbol: str) -> float:
        """
        Округляет цену в соответствии с требованиями биржи

        Args:
            price: Исходная цена
            symbol: Торговый символ

        Returns:
            Округленная цена
        """
        return self._round_value(price, symbol, 'price')

    def validate_quantity(self, quantity: float, symbol: str) -> bool:
        """
        Проверяет что количество соответствует требованиям биржи

        Args:
            quantity: Количество для проверки
            symbol: Торговый символ

        Returns:
            True если количество валидно
        """
        info = self._instruments_info.get(symbol)
        if not info:
            self.logger.warning(f"Информация об инструменте {symbol} не загружена, пропускаем валидацию")
            return True

        min_qty = info.get('min_qty')
        max_qty = info.get('max_qty')

        if min_qty and quantity < min_qty:
            self.logger.error(f"Количество {quantity} меньше минимального {min_qty}")
            return False

        if max_qty and quantity > max_qty:
            self.logger.error(f"Количество {quantity} больше максимального {max_qty}")
            return False

        return True

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
    async def get_account_balance(self, currency: str) -> float:
        """Получает баланс аккаунта для указанной валюты"""
        account = await self.client.futures_account()

        for asset in account['assets']:
            if asset['asset'] == currency:
                return float(asset['walletBalance'])
        return 0.0

    @retry_on_api_error()
    async def get_current_position(self, symbol: str) -> Optional[Dict[str, Any]]:
        """Получает текущую позицию по символу"""
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

    @retry_on_api_error()
    async def get_exact_entry_price(self, symbol: str) -> Optional[float]:
        """
        Получает точную цену входа из текущей позиции

        Args:
            symbol: Торговый символ

        Returns:
            Цена входа или None если позиции нет
        """
        try:
            positions = await self.client.futures_position_information(symbol=symbol)

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
    async def get_open_orders(self, symbol: str) -> List[Dict[str, Any]]:
        """
        Получает список открытых ордеров по символу

        Args:
            symbol: Торговый символ

        Returns:
            Список открытых ордеров
        """
        try:
            orders = await self.client.futures_get_open_orders(symbol=symbol)
            return orders
        except Exception as e:
            self.logger.error(f"Ошибка получения открытых ордеров для {symbol}: {e}")
            return []

    @retry_on_api_error()
    async def place_stop_limit_order(self, symbol: str, side: str, quantity: float,
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

            result = await self.client.futures_create_order(
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
    async def cancel_stop_order(self, symbol: str, order_id: str) -> bool:
        """
        Отменяет стоп ордер

        Args:
            symbol: Торговый символ
            order_id: ID ордера для отмены

        Returns:
            True если ордер отменен успешно, False иначе
        """
        try:
            await self.client.futures_cancel_order(
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

    async def open_long_position(self, symbol: str, quantity: float, position_side: Optional[str] = None) -> bool:
        """
        Открывает длинную позицию с готовым количеством

        Args:
            symbol: Торговый символ
            quantity: Рассчитанное количество
            position_side: 'LONG' для хеджирования, None для ONE-WAY

        Returns:
            True если позиция открыта успешно
        """
        return await self._open_position(symbol, "BUY", quantity, position_side)

    async def open_short_position(self, symbol: str, quantity: float, position_side: Optional[str] = None) -> bool:
        """
        Открывает короткую позицию с готовым количеством

        Args:
            symbol: Торговый символ
            quantity: Рассчитанное количество
            position_side: 'SHORT' для хеджирования, None для ONE-WAY

        Returns:
            True если позиция открыта успешно
        """
        return await self._open_position(symbol, "SELL", quantity, position_side)

    @retry_on_api_error()
    async def _open_position(self, symbol: str, side: str, quantity: float, position_side: Optional[str] = None) -> bool:
        """
        Открывает позицию с переданным количеством

        Args:
            symbol: Торговый символ
            side: 'BUY' или 'SELL'
            quantity: Рассчитанное и готовое количество
            position_side: 'LONG' или 'SHORT' для хеджирования, None для ONE-WAY

        Returns:
            True если успешно
        """
        rounded_quantity = self.round_quantity(quantity, symbol)

        if not self.validate_quantity(rounded_quantity, symbol):
            return False

        order_params = {
            'symbol': symbol,
            'side': side,
            'type': 'MARKET',
            'quantity': rounded_quantity
        }

        if position_side:
            order_params['positionSide'] = position_side

        await self.client.futures_create_order(**order_params)

        return True

    @retry_on_api_error()
    async def reverse_position_fast(self, symbol: str, total_quantity: float) -> bool:
        """
        Разворот позиции с указанным итоговым объемом

        Args:
            symbol: Торговый символ
            total_quantity: Итоговый объем для разворота (предыдущий + новый)

        Returns:
            True если разворот успешен, False иначе
        """
        current_position = await self.get_current_position(symbol)
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
            await self.client.futures_create_order(
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
    async def close_position(self, symbol: str) -> bool:
        """Закрывает текущую позицию по символу (HEDGE MODE - без reduceOnly)"""
        position = await self.get_current_position(symbol)
        if not position:
            return True

        opposite_side = "SELL" if position['side'] == "Buy" else "BUY"
        position_side = 'LONG' if position['side'] == "Buy" else 'SHORT'
        rounded_size = self.round_quantity(position['size'], symbol)

        await self.client.futures_create_order(
            symbol=symbol,
            side=opposite_side,
            type='MARKET',
            quantity=rounded_size,
            positionSide=position_side
        )

        self.logger.info(f"Закрыта {position['side']} позиция {symbol}, PnL: {position['unrealized_pnl']}")
        return True