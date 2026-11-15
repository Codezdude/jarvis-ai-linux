import logging
import os

from config.settings import BASE_DIR

# Ensure logs directory exists (absolute path, CWD-independent on Linux)
LOG_DIR = os.path.join(BASE_DIR, "logs")
os.makedirs(LOG_DIR, exist_ok=True)

logging.basicConfig(
    filename=os.path.join(LOG_DIR, "jarvis_system.log"),
    level=logging.INFO,
    format="[%(asctime)s] [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)

logger = logging.getLogger("JARVIS")


def log_info(message: str):
  print(f"[JARVIS] {message}")
  logger.info(message)


def log_error(message: str):
  print(f"[JARVIS ERROR] {message}")
  logger.error(message)