# src/exchanges/binance/wss.py

import websocket
import json
import threading
import time
from typing import Callable, Optional
from src.utils.logger import get_logger

logger = get_logger(__name__)


class BinancePriceStream:
    """WebSocket поток цен для Binance Futures"""

    def __init__(self, symbol: str, on_price_update: Callable[[float], None]):
        self.symbol = symbol.lower()
        self.on_price_update = on_price_update
        self.ws: Optional[websocket.WebSocketApp] = None
        self.is_running = False
        self.thread: Optional[threading.Thread] = None
        self.reconnect_attempts = 0
        self.max_reconnect_attempts = 5
        self.reconnect_delay = 5
        self._shutdown_lock = threading.Lock()
        self._reconnect_lock = threading.Lock()
        self._ws_running = False

    def start(self):
        """Запуск потока цен"""
        if self.is_running:
            logger.warning(f"Поток цен для {self.symbol} уже запущен")
            return

        self.is_running = True
        self.reconnect_attempts = 0
        self._connect()

    def stop(self):
        """Остановка потока цен"""
        with self._shutdown_lock:
            if not self.is_running:
                return

            logger.info(f"Остановка потока цен для {self.symbol}")
            self.is_running = False
            self._ws_running = False

            if self.ws:
                try:
                    self.ws.close()
                except Exception as e:
                    logger.error(f"Ошибка при закрытии WebSocket для {self.symbol}: {e}")
                finally:
                    self.ws = None

            if self.thread and self.thread.is_alive():
                self.thread.join(timeout=5)
                if self.thread.is_alive():
                    logger.warning(f"Поток {self.symbol} не завершился за 5 секунд")

            logger.info(f"Поток цен для {self.symbol} остановлен")

    def _connect(self):
        """Создает WebSocket соединение"""
        if not self.is_running:
            return

        url = f"wss://fstream.binance.com/ws/{self.symbol}@ticker"

        self.ws = websocket.WebSocketApp(
            url,
            on_message=self._on_message,
            on_error=self._on_error,
            on_close=self._on_close,
            on_open=self._on_open
        )

        self._ws_running = True
        self.thread = threading.Thread(
            target=self._run_websocket,
            daemon=True,
            name=f"BinancePrice-{self.symbol}"
        )
        self.thread.start()

    def _run_websocket(self):
        """Запускает WebSocket в отдельном потоке"""
        while self.is_running and self._ws_running:
            try:
                if self.ws and self.is_running and self._ws_running:
                    self.ws.run_forever(
                        ping_interval=20,
                        ping_timeout=10
                    )
            except Exception as e:
                if self.is_running:
                    logger.error(f"Ошибка WebSocket для {self.symbol}: {e}")

            if not self.is_running or not self._ws_running:
                break

            time.sleep(1)

    def _on_message(self, _, message):
        """Обработчик входящих сообщений"""
        try:
            data = json.loads(message)

            if 'c' in data:
                current_price = float(data['c'])
                self.on_price_update(current_price)

                if not hasattr(self, '_message_count'):
                    self._message_count = 0
                self._message_count += 1

                if self._message_count % 100 == 0:
                    logger.debug(f"Цена {self.symbol}: {current_price}")

        except json.JSONDecodeError as e:
            logger.error(f"Ошибка парсинга JSON для {self.symbol}: {e}")
        except KeyError as e:
            logger.error(f"Отсутствует поле в данных {self.symbol}: {e}")
        except ValueError as e:
            logger.error(f"Ошибка преобразования цены {self.symbol}: {e}")
        except Exception as e:
            logger.error(f"Неожиданная ошибка обработки сообщения {self.symbol}: {e}")

    def _on_error(self, _, error):
        """Обработчик ошибок WebSocket"""
        if self.is_running:
            logger.error(f"WebSocket ошибка для {self.symbol}: {error}")

    def _on_close(self, _, close_status_code, close_msg):
        """Обработчик закрытия соединения"""
        if self.is_running:
            logger.warning(f"WebSocket соединение закрыто для {self.symbol}. "
                           f"Код: {close_status_code}, Сообщение: {close_msg}")
            self._handle_reconnect()
        else:
            logger.info(f"WebSocket соединение корректно закрыто для {self.symbol}")

    def _on_open(self, _):
        """Обработчик открытия соединения"""
        logger.info(f"WebSocket соединение с потоком цен открыто для {self.symbol.upper()}")
        self.reconnect_attempts = 0

    def _handle_reconnect(self):
        """Обработка переподключения"""
        with self._reconnect_lock:
            if not self.is_running:
                return

            self.reconnect_attempts += 1

            if self.reconnect_attempts > self.max_reconnect_attempts:
                logger.error(f"Превышено максимальное количество попыток переподключения для {self.symbol}")
                self.is_running = False
                return

            logger.info(f"Попытка переподключения #{self.reconnect_attempts} для {self.symbol} "
                        f"через {self.reconnect_delay} секунд")

            self._ws_running = False

            if self.ws:
                try:
                    self.ws.close()
                except Exception as e:
                    logger.debug(f"Ошибка при закрытии WS перед переподключением: {e}")
                self.ws = None

            if self.thread and self.thread.is_alive():
                self.thread.join(timeout=3)

            time.sleep(self.reconnect_delay)

            if self.is_running:
                self._connect()