"""Bounded-memory reader for QCE's messages-array export."""
from decimal import Decimal
from pathlib import Path

import ijson
from ijson.common import ObjectBuilder


def _events(path):
    with Path(path).open('rb') as source:
        if source.read(3) != b'\xef\xbb\xbf':
            source.seek(0)
        try:
            yield from ijson.parse(source)
        except ijson.JSONError:
            raise ValueError('Malformed JSON export') from None


def iter_export_messages(path):
    # Validate first so malformed exports cannot silently commit a valid prefix.
    found = False
    root = False
    for prefix, event, _value in _events(path):
        if not prefix and event == 'start_map':
            root = True
        elif not prefix and event == 'start_array':
            raise ValueError('Expected an export object')
        if prefix == 'messages':
            if event == 'start_array':
                if found:
                    raise ValueError('Duplicate messages array')
                found = True
            elif event != 'end_array':
                raise ValueError('Expected a messages array')
    if not root or not found:
        raise ValueError('Missing messages array')

    builder = None
    depth = 0
    for prefix, event, value in _events(path):
        if prefix == 'messages.item' and builder is None:
            if event not in ('start_map', 'start_array'):
                yield value
                continue
            builder = ObjectBuilder()
        if builder is not None:
            builder.event(event, float(value) if isinstance(value, Decimal) else value)
            if event in ('start_map', 'start_array'):
                depth += 1
            elif event in ('end_map', 'end_array'):
                depth -= 1
                if depth == 0:
                    yield builder.value
                    builder = None
