# src/exchanges/binance/wss.py

import json
import asyncio
from typing import Callable, Optional
from websockets.asyncio.client import connect, ClientConnection
from websockets.exceptions import ConnectionClosed, WebSocketException
from src.utils.logger import get_logger

logger = get_logger(__name__)


class BinancePriceStream:
    """WebSocket поток цен для Binance Futures"""

    def __init__(self, symbol: str, on_price_update: Callable[[float], None]):
        self.symbol = symbol.lower()
        self.on_price_update = on_price_update
        self.stream = f"{self.symbol}@ticker"
        self.ws_url = "wss://fstream.binance.com/ws"
        self.websocket: Optional[ClientConnection] = None
        self.is_running = False
        self.task: Optional[asyncio.Task] = None

    def start(self):
        """Запуск потока цен"""
        if self.is_running:
            logger.warning(f"Поток цен для {self.symbol} уже запущен")
            return

        self.is_running = True
        loop = asyncio.get_event_loop()
        self.task = loop.create_task(self._connect_loop())
        logger.info(f"WebSocket поток для {self.symbol.upper()} запущен")

    def stop(self):
        """Остановка потока цен"""
        if not self.is_running:
            return

        logger.info(f"Остановка потока цен для {self.symbol}")
        self.is_running = False

        if self.task and not self.task.done():
            self.task.cancel()

        logger.info(f"Поток цен для {self.symbol} остановлен")

    async def _connect_loop(self):
        """Основной цикл подключения с автоматическим переподключением"""
        backoff = 1

        while self.is_running:
            try:
                logger.info(f"Подключение к потоку {self.stream}")

                self.websocket = await connect(
                    self.ws_url,
                    ping_interval=20,
                    ping_timeout=20
                )

                sub_msg = {
                    "method": "SUBSCRIBE",
                    "params": [self.stream],
                    "id": 1
                }
                await self.websocket.send(json.dumps(sub_msg))
                logger.info(f"WebSocket соединение открыто для {self.symbol.upper()}")

                backoff = 1

                await self._listen()

            except ConnectionClosed:
                if self.is_running:
                    logger.warning(f"WebSocket соединение закрыто для {self.symbol}")
                    backoff = await self._reconnect(backoff)
                else:
                    break

            except WebSocketException as e:
                if self.is_running:
                    logger.error(f"WebSocket ошибка для {self.symbol}: {e}")
                    backoff = await self._reconnect(backoff)
                else:
                    break

            except asyncio.CancelledError:
                logger.info(f"WebSocket задача отменена для {self.symbol}")
                break

            except (OSError, ConnectionError) as e:
                if self.is_running:
                    logger.error(f"Сетевая ошибка для {self.symbol}: {e}")
                    backoff = await self._reconnect(backoff)
                else:
                    break

            finally:
                await self._cleanup_connection()

    async def _listen(self):
        """Слушает входящие сообщения"""
        async for raw_message in self.websocket:
            if not self.is_running:
                break

            try:
                data = json.loads(raw_message)

                if "result" in data or data.get("e") is None:
                    continue

                if data.get("e") != "24hrTicker":
                    continue

                price_str = data.get("c")
                if not price_str:
                    continue

                current_price = float(price_str)
                self.on_price_update(current_price)

            except json.JSONDecodeError as e:
                logger.warning(f"Ошибка парсинга JSON для {self.symbol}: {e}")
            except (KeyError, ValueError) as e:
                logger.warning(f"Ошибка обработки данных для {self.symbol}: {e}")
            except Exception as e:
                logger.error(f"Неожиданная ошибка обработки сообщения {self.symbol}: {e}")

    async def _reconnect(self, backoff: int) -> int:
        """Обработка переподключения с экспоненциальной задержкой"""
        await self._cleanup_connection()

        delay = min(backoff, 30)
        logger.info(f"Переподключение для {self.symbol} через {delay} секунд")
        await asyncio.sleep(delay)

        return min(backoff * 2, 30)

    async def _cleanup_connection(self):
        """Корректное закрытие WebSocket соединения"""
        if self.websocket:
            try:
                unsub_msg = {
                    "method": "UNSUBSCRIBE",
                    "params": [self.stream],
                    "id": 2
                }
                await self.websocket.send(json.dumps(unsub_msg))
                await asyncio.sleep(0.1)
            except (ConnectionClosed, WebSocketException, OSError):
                pass

            try:
                await self.websocket.close()
            except (ConnectionClosed, WebSocketException, OSError) as e:
                logger.debug(f"Ошибка при закрытии WebSocket: {e}")
            finally:
                self.websocket = None