"""Custom logging class and setup function"""

import sys
import logging
from logging import INFO
from icloudds.database import DatabaseHandler

class IPDLogger(logging.Logger):
    """Custom logger class with support for tqdm progress bar"""

    def __init__(self, name, level=INFO):
        logging.Logger.__init__(self, name, level)
        self.tqdm = None

    # If tdqm progress bar is not set, we just write regular log messages
    def set_tqdm(self, tdqm):
        """Sets the tqdm progress bar"""
        self.tqdm = tdqm

    def set_tqdm_description(self, desc, loglevel=INFO):
        """Set tqdm progress bar description, fallback to logging"""
        if self.tqdm is None:
            self.log(loglevel, desc)
        else:
            self.tqdm.set_description(desc)

    def tqdm_write(self, message, loglevel=INFO):
        """Write to tqdm progress bar, fallback to logging"""
        if self.tqdm is None:
            self.log(loglevel, message)
        else:
            self.tqdm.write(message)

def setup_logger():
    """Set up logger and add stdout handler"""
    logging.setLoggerClass(IPDLogger)
    logger = logging.getLogger("icloudds")
    pyicloud_logger = logging.getLogger('pyicloud')

    has_stdout_handler = False
    has_database_handler = False

    for handler in logger.handlers:
        if handler.name == "stdoutLogger":
            has_stdout_handler = True

    formatter = logging.Formatter(
        fmt="%(asctime)s %(levelname)-8s %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S")

    if not has_stdout_handler:
        handler = logging.StreamHandler(stream=sys.stdout)
        handler.setFormatter(formatter)
        handler.name = "stdoutLogger"
        logger.addHandler(handler)
        pyicloud_logger.addHandler(handler)

    pyicloud_logger.disabled = logger.disabled
    pyicloud_logger.setLevel(logger.level)

    return logger

def setup_database_logger():
    """Set up logger and add stdout handler"""
    logging.setLoggerClass(IPDLogger)
    logger = logging.getLogger("icloudds")
    pyicloud_logger = logging.getLogger('pyicloud')

    #has_database_handler = False

    for handler in logger.handlers:
        if handler.name == "databaseLogger":
            logger.removeHandler(handler)
            #has_database_handler = True
    for handler in pyicloud_logger.handlers:
        if handler.name == "databaseLogger":
            pyicloud_logger.removeHandler(handler)

    formatter = logging.Formatter(
        fmt="%(asctime)s %(levelname)-8s %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S")

    #if not has_database_handler:
    handler = DatabaseHandler()
    handler.setFormatter(formatter)
    handler.name = "databaseLogger"
    logger.addHandler(handler)
    pyicloud_logger.addHandler(handler)

    return logger

