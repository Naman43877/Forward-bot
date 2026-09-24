class ContextTypes:
    DEFAULT_TYPE = object


class _H:
    def __init__(self, *a, **k):
        self.args = a


CommandHandler = CallbackQueryHandler = MessageHandler = _H


class ChatMemberHandler(_H):
    MY_CHAT_MEMBER = 1


class _Builder:
    def token(self, t):
        return self

    def post_init(self, f):
        return self

    def post_shutdown(self, f):
        return self

    def build(self):
        return Application()


class Application:
    @staticmethod
    def builder():
        return _Builder()

    def add_handler(self, h):
        pass

    def add_error_handler(self, h):
        pass

    def run_polling(self, **k):
        pass


class _F:
    def __and__(self, other):
        return self

    def __invert__(self):
        return self


class filters:
    class ChatType:
        PRIVATE = _F()

    class StatusUpdate:
        ALL = _F()

    COMMAND = _F()
