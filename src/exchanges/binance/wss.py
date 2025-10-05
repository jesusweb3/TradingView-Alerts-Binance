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
        self.reconnect_attempts = 0
        self.max_reconnect_attempts = 5
        self.task: Optional[asyncio.Task] = None
        self.first_message_received = False

    def start(self):
        """Запуск потока цен"""
        if self.is_running:
            logger.warning(f"Поток цен для {self.symbol} уже запущен")
            return

        self.is_running = True
        self.reconnect_attempts = 0
        self.first_message_received = False

        loop = asyncio.get_event_loop()
        self.task = loop.create_task(self._connect_loop())
        logger.info(f"WebSocket поток для {self.symbol.upper()} запущен")

    def stop(self):
        """Остановка потока цен"""
        if not self.is_running:
            return

        logger.info(f"Остановка потока цен для {self.symbol}")
        self.is_running = False

        if self.websocket:
            asyncio.create_task(self._close_websocket())

        if self.task and not self.task.done():
            self.task.cancel()

        logger.info(f"Поток цен для {self.symbol} остановлен")

    async def _close_websocket(self):
        """Закрытие WebSocket соединения"""
        try:
            if self.websocket:
                await self.websocket.close()
                self.websocket = None
        except Exception as e:
            logger.debug(f"Ошибка при закрытии WebSocket: {e}")

    async def _connect_loop(self):
        """Основной цикл подключения с автоматическим переподключением"""
        backoff = 1

        while self.is_running:
            try:
                logger.info(f"Подключение к потоку {self.stream}")

                websocket = await connect(
                    self.ws_url,
                    ping_interval=20,
                    ping_timeout=10,
                    close_timeout=5
                )

                self.websocket = websocket

                try:
                    sub_msg = {
                        "method": "SUBSCRIBE",
                        "params": [self.stream],
                        "id": 1
                    }
                    await websocket.send(json.dumps(sub_msg))
                    logger.info(f"WebSocket соединение с потоком цен открыто для {self.symbol.upper()}")

                    self.reconnect_attempts = 0
                    backoff = 1
                    self.first_message_received = False

                    await self._listen(websocket)

                finally:
                    await websocket.close()
                    self.websocket = None
                    logger.info(f"WebSocket соединение закрыто для {self.symbol}")

            except ConnectionClosed:
                if self.is_running:
                    logger.warning(f"WebSocket соединение закрыто для {self.symbol}")
                    backoff = await self._handle_reconnect(backoff)
                else:
                    logger.info(f"WebSocket соединение корректно закрыто для {self.symbol}")
                    break

            except ConnectionResetError as e:
                if self.is_running:
                    logger.warning(f"Соединение сброшено удаленным хостом для {self.symbol}: {e}")
                    backoff = await self._handle_reconnect(backoff)
                else:
                    break

            except WebSocketException as e:
                if self.is_running:
                    logger.error(f"WebSocket ошибка для {self.symbol}: {e}")
                    backoff = await self._handle_reconnect(backoff)
                else:
                    break

            except asyncio.CancelledError:
                logger.info(f"WebSocket задача отменена для {self.symbol}")
                break

            except Exception as e:
                if self.is_running:
                    logger.error(f"Неожиданная ошибка WebSocket для {self.symbol}: {e}")
                    backoff = await self._handle_reconnect(backoff)
                else:
                    break

    async def _listen(self, websocket: ClientConnection):
        """Слушает входящие сообщения"""
        try:
            async for raw_message in websocket:
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

                    if not self.first_message_received:
                        self.first_message_received = True
                        logger.info(f"WebSocket для {self.symbol.upper()} получает данные, текущая цена: ${current_price:.2f}")

                    self.on_price_update(current_price)

                except json.JSONDecodeError as e:
                    logger.warning(f"Ошибка парсинга JSON для {self.symbol}: {e}")
                except (KeyError, ValueError) as e:
                    logger.warning(f"Ошибка обработки данных для {self.symbol}: {e}")
                except Exception as e:
                    logger.error(f"Неожиданная ошибка обработки сообщения {self.symbol}: {e}")

        except ConnectionClosed:
            raise
        except Exception as e:
            logger.error(f"Ошибка в _listen для {self.symbol}: {e}")
            raise

    async def _handle_reconnect(self, backoff: int) -> int:
        """Обработка переподключения с экспоненциальной задержкой"""
        if not self.is_running:
            return backoff

        self.reconnect_attempts += 1

        if self.reconnect_attempts > self.max_reconnect_attempts:
            logger.error(f"Превышено максимальное количество попыток переподключения для {self.symbol}")
            self.is_running = False
            return backoff

        delay = min(backoff, 30)
        logger.info(f"Попытка переподключения #{self.reconnect_attempts} для {self.symbol} "
                    f"через {delay} секунд")

        await asyncio.sleep(delay)
        return min(backoff * 2, 30)