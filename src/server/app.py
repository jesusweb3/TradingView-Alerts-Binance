# src/server/app.py

import socket
import asyncio
from typing import Set, Optional
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request, HTTPException, Depends
from starlette.middleware.base import BaseHTTPMiddleware
import uvicorn
from src.utils.logger import get_logger
from src.config.manager import config_manager
from src.strategies.strategy import Strategy
from src.monitoring.health_monitor import health_monitor
from src.telegram.notifier import TelegramNotifier
from src.telegram.handler import initialize_telegram

logger = get_logger(__name__)


class AppState:
    """Состояние приложения"""
    def __init__(self):
        self.allowed_ips: Set[str] = set()
        self.strategy: Optional[Strategy] = None
        self.telegram_notifier: Optional[TelegramNotifier] = None


class RequestTrackingMiddleware(BaseHTTPMiddleware):
    """Middleware для отслеживания запросов в мониторе здоровья"""

    async def dispatch(self, request, call_next):
        response = await call_next(request)

        if request.url.path == "/webhook" and response.status_code == 200:
            health_monitor.record_request()

        return response


def get_app_state(request: Request) -> AppState:
    """Dependency для получения состояния приложения"""
    return request.app.state.app_state


def get_strategy(app_state: AppState = Depends(get_app_state)) -> Strategy:
    """Dependency для получения strategy"""
    if app_state.strategy is None:
        raise HTTPException(status_code=500, detail="Сервер не готов")
    return app_state.strategy


def get_allowed_ips(app_state: AppState = Depends(get_app_state)) -> Set[str]:
    """Dependency для получения разрешенных IP"""
    return app_state.allowed_ips


async def initialize_app():
    """Асинхронная инициализация приложения при старте"""
    try:
        app_state = AppState()

        server_config = config_manager.get_server_config()
        app_state.allowed_ips = set(server_config['allowed_ips'])

        telegram_config = config_manager.get_telegram_config()
        app_state.telegram_notifier = TelegramNotifier(
            bot_token=telegram_config['bot_token'],
            chat_ids=telegram_config['chat_ids']
        )

        if app_state.telegram_notifier.test_connection():
            initialize_telegram(app_state.telegram_notifier)
            logger.info("Telegram уведомления инициализированы")
        else:
            raise RuntimeError("Не удалось подключиться к Telegram боту")

        trading_config = config_manager.get_trading_config()
        symbol = trading_config['symbol']

        stop_config = config_manager.get_trailing_stop_config()
        if stop_config['enabled']:
            logger.info(f"Актив: {symbol} с включенным стопом: активация {stop_config['activation_percent']}%, "
                        f"стоп {stop_config['stop_percent']}%")
        else:
            logger.info(f"Актив: {symbol} с отключенным стопом")

        app_state.strategy = Strategy()

        app.state.app_state = app_state  # type: ignore[attr-defined]

        health_monitor.start_monitoring(app_state.strategy)

    except Exception as e:
        logger.error(f"Ошибка инициализации приложения: {e}")
        raise


async def cleanup_app():
    """Очистка ресурсов при завершении приложения"""
    try:
        logger.info("Начинается процедура завершения приложения...")

        health_monitor.stop_monitoring()

        app_state: AppState = app.state.app_state  # type: ignore
        if app_state.strategy:
            app_state.strategy.cleanup()
            logger.info("Ресурсы стратегии корректно завершены")
            app_state.strategy = None

        await asyncio.sleep(0.5)

        logger.info("Все ресурсы приложения успешно очищены")

    except Exception as e:
        logger.error(f"Ошибка при очистке ресурсов: {e}")


@asynccontextmanager
async def lifespan(_: FastAPI):
    """Управление жизненным циклом FastAPI приложения"""
    await initialize_app()
    yield
    await cleanup_app()


app = FastAPI(lifespan=lifespan)
app.add_middleware(RequestTrackingMiddleware)


def is_port_in_use(port: int) -> bool:
    """Проверяет занят ли порт"""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind(('0.0.0.0', port))
            return False
        except OSError:
            return True


def get_client_ip(request: Request) -> str:
    """Получает IP клиента с учетом прокси"""
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        return forwarded.split(",")[0].strip()

    real_ip = request.headers.get("X-Real-IP")
    if real_ip:
        return real_ip.strip()

    return request.client.host


@app.get("/health")
async def health_check():
    """Health check endpoint"""
    return {"status": "ok"}


@app.post("/webhook")
async def webhook_handler(
    request: Request,
    strategy: Strategy = Depends(get_strategy),
    allowed_ips: Set[str] = Depends(get_allowed_ips)
):
    """Webhook endpoint для приема сигналов от TradingView"""
    try:
        client_ip = get_client_ip(request)

        if client_ip not in allowed_ips:
            logger.warning(f"Запрос от неразрешенного IP: {client_ip}")
            raise HTTPException(status_code=403, detail="Forbidden")

        raw_body = await request.body()
        message = raw_body.decode('utf-8').strip()

        logger.info(f"Получен webhook от {client_ip}: {message}")

        if not message:
            logger.warning("В webhook нет текстового сообщения")
            return {"status": "error", "message": "Нет текстового сообщения"}

        result = strategy.process_webhook(message)

        if result is None:
            return {"status": "ignored", "message": "Сигнал не распознан"}

        return result

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Ошибка в webhook_handler: {e}")
        raise HTTPException(status_code=500, detail=str(e))


def start_server_sync():
    """Синхронная функция запуска сервера"""
    ensure_port_80_free()

    server_ip = get_server_ip()
    logger.info(f"Ваш хук для TradingView: http://{server_ip}/webhook")

    uvicorn.run(
        app,
        host="0.0.0.0",
        port=80,
        http="httptools",
        log_level="error"
    )


def ensure_port_80_free():
    """Обеспечивает освобождение порта 80"""
    import time

    if not is_port_in_use(80):
        return

    logger.warning("Порт 80 занят, освобождаем...")

    stop_services_on_port_80()
    time.sleep(2)

    if not is_port_in_use(80):
        logger.info("Порт 80 освобожден после остановки служб")
        return

    kill_processes_on_port_80()
    time.sleep(2)

    if is_port_in_use(80):
        raise RuntimeError("Не удалось освободить порт 80")

    logger.info("Порт 80 успешно освобожден")


def stop_services_on_port_80():
    """Останавливает службы Windows на порту 80"""
    import subprocess

    logger.info("Порт 80 занят, останавливаем веб-службы...")

    services_to_stop = ['w3svc', 'http', 'iisadmin']

    for service in services_to_stop:
        try:
            result = subprocess.run(
                ['net', 'stop', service],
                capture_output=True,
                text=True,
                timeout=30
            )
            if result.returncode == 0:
                logger.info(f"Служба {service} остановлена")
            else:
                logger.warning(f"Не удалось остановить службу {service}: {result.stderr}")
        except subprocess.TimeoutExpired:
            logger.warning(f"Таймаут при остановке службы {service}")
        except Exception as e:
            logger.warning(f"Ошибка при остановке службы {service}: {e}")


def kill_processes_on_port_80():
    """Принудительно завершает процессы на порту 80"""
    import subprocess

    logger.info("Принудительное завершение процессов на порту 80...")

    try:
        result = subprocess.run(
            ['netstat', '-ano'],
            capture_output=True,
            text=True,
            timeout=10
        )

        if result.returncode == 0:
            lines = result.stdout.split('\n')
            pids_to_terminate = set()

            for line in lines:
                if ':80 ' in line and 'LISTENING' in line:
                    parts = line.split()
                    if len(parts) >= 5:
                        pid = parts[-1]
                        if pid.isdigit():
                            pids_to_terminate.add(pid)

            for pid in pids_to_terminate:
                try:
                    subprocess.run(['taskkill', '/PID', pid, '/F'],
                                   capture_output=True, timeout=10)
                    logger.info(f"Процесс PID {pid} завершен")
                except Exception as e:
                    logger.warning(f"Не удалось завершить процесс PID {pid}: {e}")

    except Exception as e:
        logger.error(f"Ошибка при завершении процессов: {e}")


def get_server_ip():
    """Получает внешний IP сервера"""
    import requests

    try:
        response = requests.get('https://ipinfo.io/ip', timeout=5)
        return response.text.strip()
    except Exception as e:
        logger.error(f"Не удалось получить внешний IP: {e}")
        raise RuntimeError("Ошибка получения внешнего IP сервера")