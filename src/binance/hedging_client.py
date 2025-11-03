# src/binance/hedging_client.py

from binance import AsyncClient
from binance.exceptions import BinanceAPIException
from typing import Optional, Dict, Any, List
from src.utils.logger import get_logger
from src.utils.retry_handler import retry_on_api_error


class HedgingBinanceClient:
    """Async клиент Binance для hedging стратегии"""

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
        await self.enable_hedge_mode()

        self.logger.info(f"Актив {self.symbol} инициализирован: плечо {self.leverage}x, HEDGE режим")

    async def close(self):
        """Закрытие клиента"""
        if self.client:
            await self.client.close_connection()
            self.logger.info("Binance клиент закрыт")

    async def enable_hedge_mode(self) -> bool:
        """
        Включает режим хеджирования (dualSidePosition=true)

        Returns:
            True если успешно или уже был включен
        """
        try:
            current_mode = await self.client.futures_get_position_mode()
            is_hedge_enabled = current_mode.get('dualSidePosition', False)

            if is_hedge_enabled:
                self.logger.info("Режим хеджирования уже включен")
                return True

            await self.client.futures_change_position_mode(dualSidePosition='true')
            self.logger.info("Режим хеджирования включен")
            return True

        except BinanceAPIException as e:
            if "No need to change position side" in str(e):
                self.logger.info("Режим хеджирования уже включен")
                return True
            elif "Modify Hedge mode is not allowed" in str(e):
                self.logger.error(f"Нельзя изменить режим хеджирования: есть открытые позиции/ордера")
                return False
            else:
                self.logger.error(f"Binance API ошибка при включении хеджирования: {e}")
                return False
        except Exception as e:
            self.logger.error(f"Ошибка включения режима хеджирования: {e}")
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
                'min_qty': None,
                'price_precision': None,
                'tick_size': None
            }

            for filter_item in symbol_info['filters']:
                if filter_item['filterType'] == 'LOT_SIZE':
                    info['qty_step'] = float(filter_item['stepSize'])
                    info['min_qty'] = float(filter_item['minQty'])
                elif filter_item['filterType'] == 'PRICE_FILTER':
                    info['tick_size'] = float(filter_item['tickSize'])
                    info['price_precision'] = filter_item

            self._instruments_info[symbol] = info

        except Exception as e:
            self.logger.error(f"Ошибка загрузки информации об инструменте {symbol}: {e}")
            raise

    @retry_on_api_error()
    async def get_current_position(self, symbol: str, position_side: str = 'LONG') -> Optional[Dict[str, Any]]:
        """
        Получает текущую открытую позицию по символу и стороне

        Args:
            symbol: Торговый символ
            position_side: 'LONG' или 'SHORT'

        Returns:
            Словарь {side, size, entry_price, unrealized_pnl} или None если позиции нет
        """
        try:
            positions = await self.client.futures_position_information(symbol=symbol)

            if positions:
                for position in positions:
                    if position.get('positionSide') == position_side:
                        size = abs(float(position['positionAmt']))

                        if size > 0:
                            return {
                                'side': position_side,
                                'size': size,
                                'entry_price': float(position['entryPrice']),
                                'unrealized_pnl': float(position['unRealizedProfit'])
                            }

            return None

        except Exception as e:
            self.logger.error(f"Ошибка получения позиции {position_side} для {symbol}: {e}")
            return None

    @retry_on_api_error()
    async def get_all_positions(self, symbol: str) -> List[Dict[str, Any]]:
        """
        Получает все открытые позиции (LONG и SHORT) в hedge mode

        Args:
            symbol: Торговый символ

        Returns:
            Список открытых позиций с информацией
        """
        try:
            positions = await self.client.futures_position_information(symbol=symbol)
            result = []

            if positions:
                for position in positions:
                    size = abs(float(position['positionAmt']))
                    if size > 0:
                        result.append({
                            'side': position['positionSide'],
                            'size': size,
                            'entry_price': float(position['entryPrice']),
                            'unrealized_pnl': float(position['unRealizedProfit'])
                        })

            return result

        except Exception as e:
            self.logger.error(f"Ошибка получения всех позиций для {symbol}: {e}")
            return []

    @retry_on_api_error()
    async def get_exact_entry_price(self, symbol: str, position_side: str = 'LONG') -> Optional[float]:
        """
        Получает точную цену входа из позиции

        Args:
            symbol: Торговый символ
            position_side: 'LONG' или 'SHORT'

        Returns:
            Цена входа или None если позиции нет
        """
        try:
            positions = await self.client.futures_position_information(symbol=symbol)

            if positions:
                for position in positions:
                    if position.get('positionSide') == position_side:
                        position_amt = float(position['positionAmt'])

                        if position_amt != 0:
                            return float(position['entryPrice'])

            return None

        except Exception as e:
            self.logger.error(f"Ошибка получения цены входа для {symbol} ({position_side}): {e}")
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
    async def open_long_position(self, symbol: str, quantity: float) -> bool:
        """
        Открывает длинную позицию рыночным ордером

        Args:
            symbol: Торговый символ
            quantity: Количество контрактов

        Returns:
            True если успешно, False иначе
        """
        try:
            await self.client.futures_create_order(
                symbol=symbol,
                side='BUY',
                type='MARKET',
                quantity=quantity,
                positionSide='LONG'
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
            quantity: Количество контрактов

        Returns:
            True если успешно, False иначе
        """
        try:
            await self.client.futures_create_order(
                symbol=symbol,
                side='SELL',
                type='MARKET',
                quantity=quantity,
                positionSide='SHORT'
            )

            self.logger.info(f"SHORT позиция открыта: {symbol} x{quantity}")
            return True

        except Exception as e:
            self.logger.error(f"Ошибка открытия SHORT позиции {symbol}: {e}")
            return False

    @retry_on_api_error()
    async def close_position(self, symbol: str, position_side: str, quantity: float) -> bool:
        """
        Закрывает позицию маркет-ордером

        Args:
            symbol: Торговый символ
            position_side: 'LONG' или 'SHORT'
            quantity: Количество контрактов

        Returns:
            True если успешно, False иначе
        """
        try:
            close_side = 'SELL' if position_side == 'LONG' else 'BUY'

            await self.client.futures_create_order(
                symbol=symbol,
                side=close_side,
                type='MARKET',
                quantity=quantity,
                positionSide=position_side
            )

            self.logger.info(f"Позиция {position_side} закрыта: {symbol} x{quantity}")
            return True

        except Exception as e:
            self.logger.error(f"Ошибка закрытия позиции {position_side} {symbol}: {e}")
            return False

    @retry_on_api_error()
    async def place_stop_loss_order(self, symbol: str, position_side: str,
                                    stop_price: float) -> Optional[Dict[str, Any]]:
        """
        Выставляет STOP_MARKET стоп-лосс ордер с closePosition=True

        Args:
            symbol: Торговый символ
            position_side: 'LONG' или 'SHORT'
            stop_price: Цена активации стопа

        Returns:
            Результат размещения ордера или None при ошибке
        """
        try:
            close_side = 'SELL' if position_side == 'LONG' else 'BUY'

            result = await self.client.futures_create_order(
                symbol=symbol,
                side=close_side,
                type='STOP_MARKET',
                stopPrice=str(stop_price),
                positionSide=position_side,
                closePosition=True,
                timeInForce='GTE_GTC',
                workingType='MARK_PRICE',
                priceProtect=True
            )

            return result

        except Exception as e:
            self.logger.error(f"Ошибка выставления стопа для {symbol} ({position_side}): {e}")
            return None

    @retry_on_api_error()
    async def update_stop_loss_order(self, symbol: str, position_side: str,
                                     new_stop_price: float) -> bool:
        """
        Обновляет существующий стоп-лосс ордер (отменяет старый, выставляет новый)

        Args:
            symbol: Торговый символ
            position_side: 'LONG' или 'SHORT'
            new_stop_price: Новая цена стопа

        Returns:
            True если успешно, False иначе
        """
        try:
            close_side = 'SELL' if position_side == 'LONG' else 'BUY'

            orders = await self.get_open_orders(symbol)

            old_stop_found = False
            for order in orders:
                if (order.get('type') == 'STOP_MARKET' and
                        order.get('side') == close_side and
                        order.get('positionSide') == position_side):
                    await self.client.futures_cancel_order(
                        symbol=symbol,
                        orderId=order['orderId']
                    )
                    self.logger.info(
                        f"Старый стоп отменён: {symbol} ({position_side}), "
                        f"была цена {order.get('stopPrice')}"
                    )
                    old_stop_found = True
                    break

            if not old_stop_found:
                self.logger.warning(f"Старый стоп не найден для {symbol} ({position_side})")

            result = await self.place_stop_loss_order(
                symbol=symbol,
                position_side=position_side,
                stop_price=new_stop_price
            )

            return result is not None

        except Exception as e:
            self.logger.error(f"Ошибка обновления стопа для {symbol} ({position_side}): {e}")
            return False

    @retry_on_api_error()
    async def cancel_stop_loss_order(self, symbol: str, order_id: str) -> bool:
        """
        Отменяет стоп-лосс ордер по ID

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

            self.logger.info(f"Стоп ордер {order_id} для {symbol} отменен")
            return True

        except BinanceAPIException as e:
            if e.code == -2011:
                self.logger.warning(f"Стоп ордер {order_id} уже не существует или был исполнен")
                return True
            else:
                self.logger.error(f"Binance API ошибка отмены ордера {order_id}: {e}")
                return False
        except Exception as e:
            self.logger.error(f"Ошибка отмены стопа {order_id} для {symbol}: {e}")
            return False

    @retry_on_api_error()
    async def cancel_all_stop_losses(self, symbol: str) -> bool:
        """
        Отменяет все STOP_MARKET ордера по символу

        Args:
            symbol: Торговый символ

        Returns:
            True если все стопы отменены или их не было
        """
        try:
            orders = await self.get_open_orders(symbol)

            cancelled_count = 0
            for order in orders:
                if order.get('type') == 'STOP_MARKET':
                    await self.client.futures_cancel_order(
                        symbol=symbol,
                        orderId=order['orderId']
                    )
                    self.logger.info(
                        f"Отменён стоп {order['orderId']} на {order.get('stopPrice')} "
                        f"({order.get('positionSide')})"
                    )
                    cancelled_count += 1

            if cancelled_count > 0:
                self.logger.info(f"Отменено {cancelled_count} стоп ордеров для {symbol}")

            return True

        except Exception as e:
            self.logger.error(f"Ошибка отмены всех стопов для {symbol}: {e}")
            return False

    def round_price(self, symbol: str, price: float) -> float:
        """
        Округляет цену по правилам биржи (по tickSize)

        Args:
            symbol: Торговый символ
            price: Цена для округления

        Returns:
            Округленная цена
        """
        info = self._instruments_info.get(symbol)
        if not info or not info.get('tick_size'):
            self.logger.warning(f"Информация о precision для {symbol} не загружена, возвращаем исходную цену")
            return price

        tick_size = info['tick_size']
        rounded_price = round(price / tick_size) * tick_size

        return round(rounded_price, 8)

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

    def calculate_activation_price(self, entry_price: float, position_side: str,
                                   activation_pnl: float) -> float:
        """
        Рассчитывает цену активации хеджа по заданному PNL проценту

        Формула:
        LONG:  activation_price = entry_price + (pnl / (100 * leverage))
        SHORT: activation_price = entry_price - (pnl / (100 * leverage))

        Примеры:
        LONG: entry=4000, pnl=-5%, leverage=4
          → 4000 + (-5 / 400) = 4000 - 1.25% = 3950$
        SHORT: entry=4000, pnl=-5%, leverage=4
          → 4000 - (-5 / 400) = 4000 + 1.25% = 4050$

        Args:
            entry_price: Цена входа
            position_side: 'LONG' или 'SHORT'
            activation_pnl: PNL процент (обычно отрицательный, например -5)

        Returns:
            Целевая цена для активации хеджа (округленная по правилам биржи)
        """
        movement = activation_pnl / (100 * self.leverage)

        if position_side == 'LONG':
            result = entry_price + movement
        else:
            result = entry_price - movement

        return self.round_price(self.symbol, result)

    def calculate_stop_loss_price(self, entry_price: float, position_side: str,
                                  stop_pnl: float) -> float:
        """
        Рассчитывает цену стоп-лосса по заданному PNL проценту

        Формула:
        LONG:  sl_price = entry_price + (pnl / (100 * leverage))
        SHORT: sl_price = entry_price - (pnl / (100 * leverage))

        Примеры:
        SHORT хедж: entry=3950, pnl=-3%, leverage=4
          → 3950 - (-3 / 400) = 3950 + 0.75% = 3979.62$
        LONG хедж: entry=3950, pnl=-3%, leverage=4
          → 3950 + (-3 / 400) = 3950 - 0.75% = 3920.38$

        Args:
            entry_price: Цена входа
            position_side: 'LONG' или 'SHORT'
            stop_pnl: PNL процент (обычно отрицательный, например -3)

        Returns:
            Цена стоп-лосса (округленная по правилам биржи)
        """
        movement = stop_pnl / (100 * self.leverage)

        if position_side == 'LONG':
            result = entry_price + movement
        else:
            result = entry_price - movement

        return self.round_price(self.symbol, result)

    def calculate_trigger_price(self, entry_price: float, position_side: str,
                                trigger_pnl: float) -> float:
        """
        Рассчитывает цену триггера (когда PNL достигнет положительного значения)

        Формула:
        LONG:  trigger_price = entry_price + (pnl / (100 * leverage))
        SHORT: trigger_price = entry_price - (pnl / (100 * leverage))

        Примеры:
        SHORT хедж: entry=3950, pnl=3%, leverage=4
          → 3950 - (3 / 400) = 3950 - 0.75% = 3920.37$
        LONG хедж: entry=3950, pnl=3%, leverage=4
          → 3950 + (3 / 400) = 3950 + 0.75% = 3979.62$

        Args:
            entry_price: Цена входа
            position_side: 'LONG' или 'SHORT'
            trigger_pnl: PNL процент (обычно положительный, например 3)

        Returns:
            Цена триггера (округленная по правилам биржи)
        """
        movement = trigger_pnl / (100 * self.leverage)

        if position_side == 'LONG':
            result = entry_price + movement
        else:
            result = entry_price - movement

        return self.round_price(self.symbol, result)

    def calculate_new_stop_price(self, entry_price: float, position_side: str,
                                 tp_pnl: float) -> float:
        """
        Рассчитывает цену нового стопа (TP в профите) - страховка профита

        Формула (разная для LONG и SHORT):
        LONG хедж:
          - tp_price = entry_price * (1 + pnl / (100 * leverage))
          - Цена ВЫШЕ entry (в профите для LONG)

        SHORT хедж:
          - tp_price = entry_price * (1 - pnl / (100 * leverage))
          - Цена НИЖЕ entry (в профите для SHORT)

        Примеры (tp_pnl=2%, leverage=4):
        LONG хедж: entry=4000
          → 4000 * (1 + 0.005) = 4020$ (выше entry, в профите)
        SHORT хедж: entry=4000
          → 4000 * (1 - 0.005) = 3980$ (ниже entry, в профите)

        Args:
            entry_price: Цена входа хеджа
            position_side: 'LONG' или 'SHORT'
            tp_pnl: PNL процент для нового стопа (обычно положительный, например 2)

        Returns:
            Цена нового стоп-лосса (в профите, округленная по правилам биржи)
        """
        movement = tp_pnl / (100 * self.leverage)

        if position_side == 'LONG':
            result = entry_price * (1 + movement)
        else:  # SHORT
            result = entry_price * (1 - movement)

        return self.round_price(self.symbol, result)

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