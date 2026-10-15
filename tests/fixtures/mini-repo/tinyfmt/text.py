import six


def ensure_text(value, encoding="utf-8"):
    if isinstance(value, six.binary_type):
        return value.decode(encoding)
    return six.text_type(value)


def title_words(value):
    return " ".join(w.capitalize() for w in ensure_text(value).split())
