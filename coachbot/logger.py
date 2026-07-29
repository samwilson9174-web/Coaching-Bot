"""logger.py — consistent structured logging across the bot."""
import logging
import sys


def get_logger(name="coachbot"):
    logger = logging.getLogger(name)
    if logger.handlers:
        return logger
    logger.setLevel(logging.INFO)
    h = logging.StreamHandler(sys.stdout)
    fmt = logging.Formatter("%(asctime)s %(levelname)-7s [%(name)s] %(message)s",
                            datefmt="%Y-%m-%d %H:%M:%S")
    h.setFormatter(fmt)
    logger.addHandler(h)
    return logger
