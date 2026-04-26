import logging

colors = {
    "DEBUG": "\033[94m",
    "INFO": "\033[92m",
    "WARNING": "\033[93m",
    "ERROR": "\033[91m",
    "CRITICAL": "\033[95m",
    "ENDC": "\033[0m",
}


class ColoredFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        levelname = record.levelname
        if levelname in colors:
            record.levelname = f"{colors[levelname]}{levelname}{colors['ENDC']}"
        try:
            return super().format(record)
        finally:
            record.levelname = levelname


logger = logging.getLogger("Maya")
logger.setLevel(logging.INFO)

if not logger.handlers:
    ch = logging.StreamHandler()
    ch.setLevel(logging.INFO)
    formatter = ColoredFormatter("%(asctime)s - %(levelname)s - %(message)s")
    ch.setFormatter(formatter)
    logger.addHandler(ch)
