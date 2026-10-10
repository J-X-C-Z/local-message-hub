"""Module-local diagnostics that never emit chat identifiers or file paths."""

import builtins
import logging
import sys


class PrivateDiagnosticFilter(logging.Filter):
    def filter(self, record):
        record.msg = "WeChat local reader diagnostic"
        record.args = ()
        record.exc_info = None
        record.exc_text = None
        record.stack_info = None
        return True


def private_logger(name):
    logger = logging.getLogger(name)
    logger.addFilter(PrivateDiagnosticFilter())
    return logger


def diagnostic_print(*args, **kwargs):
    builtins.print("[wechat] Local reader diagnostic", file=sys.stderr)
