# main.py

import os
import sys
from src.utils.logger import get_logger
from src.server.app import start_server_sync

if __name__ == "__main__":
    if sys.platform == 'win32':
        os.system('cls')
    else:
        os.system('clear')

    logger = get_logger(__name__)
    logger.info("Trading Bot запущен")

    start_server_sync()