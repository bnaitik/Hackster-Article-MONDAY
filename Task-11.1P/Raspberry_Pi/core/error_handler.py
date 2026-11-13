import logging

logger = logging.getLogger("smart_desk_bot")

def log_exception(message: str, error: Exception) -> None:
    """Record an exception with its traceback."""
    logger.error(
        "%s: %s",
        message,
        error,
        exc_info=(type(error), error, error.__traceback__),
    )
