import logging
import os

# Ensure logs directory exists
os.makedirs("logs", exist_ok=True)

logging.basicConfig(
    filename="logs/jarvis_system.log",
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