"""Test-only stand-in for python-telegram-bot. Not shipped."""


class InlineKeyboardButton:
    def __init__(self, text, url=None, callback_data=None):
        self.text, self.url, self.callback_data = text, url, callback_data

    def __repr__(self):
        return f"Btn({self.text!r}, url={self.url!r}, cb={self.callback_data!r})"


class InlineKeyboardMarkup:
    def __init__(self, inline_keyboard):
        self.inline_keyboard = inline_keyboard

    def __repr__(self):
        return f"IKM({self.inline_keyboard!r})"


class KeyboardButton:
    def __init__(self, text):
        self.text = text

    def __repr__(self):
        return f"Key({self.text!r})"


class ReplyKeyboardMarkup:
    def __init__(self, keyboard, resize_keyboard=False, is_persistent=False,
                 input_field_placeholder=None):
        self.keyboard = keyboard
        self.resize_keyboard = resize_keyboard
        self.is_persistent = is_persistent
        self.input_field_placeholder = input_field_placeholder

    def __repr__(self):
        return f"RKM({self.keyboard!r}, placeholder={self.input_field_placeholder!r})"


class ReplyKeyboardRemove:
    def __repr__(self):
        return "RKR()"


class MessageId:
    def __init__(self, message_id):
        self.message_id = message_id


class Message:
    def __init__(self, message_id=1, chat_id=999, text=None,
                 media_group_id=None, poll=None):
        self.message_id = message_id
        self.chat_id = chat_id
        self.text = text
        self.media_group_id = media_group_id
        self.poll = poll


class Bot:
    pass


class Update:
    ALL_TYPES = ["message", "callback_query", "my_chat_member"]
