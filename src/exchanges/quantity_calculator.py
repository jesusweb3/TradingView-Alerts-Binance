# src/exchanges/quantity_calculator.py

from abc import ABC, abstractmethod
from typing import Dict, Any
from src.utils.logger import get_logger

logger = get_logger(__name__)


class QuantityCalculator(ABC):
    """Базовый класс для расчета количества в торговых операциях"""

    def __init__(self, leverage: int):
        self.leverage = leverage
        self._instruments_info: Dict[str, Dict[str, Any]] = {}

    @abstractmethod
    def _fetch_instrument_info(self, symbol: str) -> Dict[str, Any]:
        """
        Получает информацию об инструменте с биржи

        Returns:
            Словарь с параметрами инструмента:
            {
                'qty_step': float,
                'min_qty': float,
                'qty_precision': int,
                'tick_size': float,
                'price_precision': int
            }
        """
        pass

    def get_instrument_info(self, symbol: str) -> Dict[str, Any]:
        """Получает информацию об инструменте с кешированием"""
        if symbol in self._instruments_info:
            return self._instruments_info[symbol]

        info = self._fetch_instrument_info(symbol)
        self._instruments_info[symbol] = info

        return info

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

        logger.info(f"Расчет для {symbol}: {total_value} / {current_price} = {rounded_quantity}")
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
        info = self.get_instrument_info(symbol)

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
        info = self.get_instrument_info(symbol)
        min_qty = info.get('min_qty')
        max_qty = info.get('max_qty')

        if min_qty and quantity < min_qty:
            logger.error(f"Количество {quantity} меньше минимального {min_qty}")
            return False

        if max_qty and quantity > max_qty:
            logger.error(f"Количество {quantity} больше максимального {max_qty}")
            return False

        return True