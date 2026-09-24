class TelegramError(Exception):
    pass


class BadRequest(TelegramError):
    pass


class Forbidden(TelegramError):
    pass


class RetryAfter(TelegramError):
    def __init__(self, retry_after):
        super().__init__(f"retry after {retry_after}")
        self.retry_after = retry_after
