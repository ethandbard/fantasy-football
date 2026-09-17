"""Tiny helpers shared by the tool modules."""


def text(body):
    return {"content": [{"type": "text", "text": body}]}


def err(body):
    return {"content": [{"type": "text", "text": body}], "is_error": True}
