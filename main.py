# main.py

from src.utils.logger import get_logger
from src.server.app import start_server_sync

if __name__ == "__main__":
    logger = get_logger(__name__)
    logger.info("Trading Bot запущен")

    start_server_sync()