# src/exchanges/binance/wss.py

import json
import asyncio
from typing import Callable, Optional
from datetime import datetime, timezone
from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed
from src.utils.logger import get_logger

logger = get_logger(__name__)


class BinancePriceStream:
    """WebSocket поток цен для Binance Futures с автоматическим переподключением"""

    def __init__(self, symbol: str, on_price_update: Callable[[float], None]):
        self.symbol = symbol.lower()
        self.on_price_update = on_price_update
        self.stream = f"{self.symbol}@ticker"
        self.ws_url = f"wss://fstream.binance.com/ws/{self.stream}"
        self.is_running = False
        self.task: Optional[asyncio.Task] = None

        self.last_price: Optional[float] = None
        self.last_price_update: Optional[datetime] = None
        self.connection_count = 0
        self.last_successful_connection: Optional[datetime] = None
        self.is_connected = False
        self.disconnection_start: Optional[datetime] = None
        self.long_disconnection_notified = False

    def start(self):
        """Запуск потока цен"""
        if self.is_running:
            logger.warning(f"Поток цен для {self.symbol} уже запущен")
            return

        self.is_running = True
        loop = asyncio.get_event_loop()
        self.task = loop.create_task(self._connection_loop())
        logger.info(f"Запускаем WebSocket поток для {self.symbol.upper()}")

    def stop(self):
        """Остановка потока цен"""
        if not self.is_running:
            return

        logger.info(f"Остановка потока цен для {self.symbol}")
        self.is_running = False

        if self.task and not self.task.done():
            self.task.cancel()

        logger.info(f"Поток цен для {self.symbol} остановлен")

    async def _connection_loop(self):
        """
        Основной цикл с автоматическим переподключением

        Использует встроенный механизм websockets 15.0.1:
        - async for автоматически переподключается при ошибках
        - Exponential backoff от 3 до 60 секунд
        - Автоматическая обработка retryable errors
        """
        async for websocket in connect(
                self.ws_url,
                open_timeout=20,
                ping_interval=20,
                ping_timeout=20,
                close_timeout=10,
                logger=logger
        ):
            try:
                self._on_connection_established()
                self.is_connected = True

                async for raw_message in websocket:
                    if not self.is_running:
                        break

                    await self._process_message(raw_message)

            except ConnectionClosed as e:
                self.is_connected = False

                if not self.is_running:
                    logger.info(f"WebSocket для {self.symbol} закрыт по запросу")
                    break

                logger.warning(f"WebSocket соединение закрыто для {self.symbol}: {e.rcvd}")
                self._on_connection_lost()
                continue

            except asyncio.CancelledError:
                self.is_connected = False
                logger.info(f"WebSocket задача отменена для {self.symbol}")
                break

            except Exception as e:
                self.is_connected = False
                logger.error(f"Неожиданная ошибка в WebSocket для {self.symbol}: {e}")
                self._on_connection_lost()
                continue

    def _on_connection_established(self):
        """Обработчик успешного подключения"""
        self.connection_count += 1
        self.last_successful_connection = datetime.now(timezone.utc)

        if self.connection_count == 1:
            logger.info(f"WebSocket подключен к {self.symbol.upper()}, ожидаем первую цену...")
        else:
            if self.disconnection_start:
                downtime = (datetime.now(timezone.utc) - self.disconnection_start).total_seconds()
                logger.info(
                    f"WebSocket переподключен для {self.symbol.upper()} "
                    f"(попытка #{self.connection_count}, downtime {downtime:.1f}s)"
                )

        self.disconnection_start = None
        self.long_disconnection_notified = False

    def _on_connection_lost(self):
        """Обработчик потери соединения"""
        if not self.disconnection_start:
            self.disconnection_start = datetime.now(timezone.utc)

        if self.disconnection_start and not self.long_disconnection_notified:
            downtime = (datetime.now(timezone.utc) - self.disconnection_start).total_seconds()

            if downtime > 300:
                logger.error(
                    f"WebSocket для {self.symbol.upper()} отключен более 5 минут. "
                    f"Последняя цена: ${self.last_price:.2f if self.last_price else 0}"
                )
                self.long_disconnection_notified = True

    async def _process_message(self, raw_message: str):
        """Обработка входящего сообщения"""
        try:
            data = json.loads(raw_message)

            if data.get("e") != "24hrTicker":
                return

            price_str = data.get("c")
            if not price_str:
                return

            current_price = float(price_str)

            self.last_price = current_price
            self.last_price_update = datetime.now(timezone.utc)

            if self.connection_count == 1 and self.last_price_update:
                if (datetime.now(timezone.utc) - self.last_successful_connection).total_seconds() < 2:
                    logger.info(
                        f"WebSocket первая цена получена для {self.symbol.upper()}: ${current_price:.2f}"
                    )

            self.on_price_update(current_price)

        except json.JSONDecodeError as e:
            logger.warning(f"Ошибка парсинга JSON для {self.symbol}: {e}")
        except (KeyError, ValueError, TypeError) as e:
            logger.warning(f"Ошибка обработки данных для {self.symbol}: {e}")
        except Exception as e:
            logger.error(f"Неожиданная ошибка обработки сообщения {self.symbol}: {e}")

    def get_last_price(self) -> Optional[float]:
        """Возвращает последнюю известную цену"""
        return self.last_price

    def get_connection_stats(self) -> dict:
        """Возвращает статистику соединения"""
        stats = {
            'symbol': self.symbol.upper(),
            'is_running': self.is_running,
            'is_connected': self.is_connected,
            'connection_count': self.connection_count,
            'last_price': self.last_price,
            'last_price_update': self.last_price_update,
            'last_successful_connection': self.last_successful_connection
        }

        if self.disconnection_start:
            stats['current_downtime_seconds'] = (
                    datetime.now(timezone.utc) - self.disconnection_start
            ).total_seconds()

        return stats

    def is_healthy(self) -> bool:
        """
        Проверяет здоровье соединения на основе получения данных

        Returns:
            True если получали данные в последние 60 секунд
        """
        if not self.last_price_update:
            return False

        time_since_last_update = (
                datetime.now(timezone.utc) - self.last_price_update
        ).total_seconds()

        return time_since_last_update < 60