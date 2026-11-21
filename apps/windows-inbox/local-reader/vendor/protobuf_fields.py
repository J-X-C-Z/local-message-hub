"""Bounded protobuf wire reader for locally observed WeCom payload profiles."""


def _varint(data, offset):
    value = 0
    for shift in range(0, 70, 7):
        if offset >= len(data):
            break
        byte = data[offset]
        offset += 1
        if shift == 63 and byte > 1:
            break
        value |= (byte & 127) << shift
        if not byte & 128:
            return value, offset
    raise ValueError("invalid protobuf varint")


def ordered_fields(blob):
    if not isinstance(blob, (bytes, bytearray, memoryview)) or len(blob) > 16 * 1024 * 1024:
        raise ValueError("unsupported protobuf payload")
    data = bytes(blob)
    result, offset, count = [], 0, 0
    while offset < len(data):
        tag, offset = _varint(data, offset)
        number, wire = tag >> 3, tag & 7
        count += 1
        if not 0 < number <= 0x1fffffff or count > 100000:
            raise ValueError("invalid protobuf field")
        if wire == 0:
            value, offset = _varint(data, offset)
        elif wire in (1, 5):
            end = offset + (8 if wire == 1 else 4)
            value = int.from_bytes(data[offset:end], "little")
            offset = end
        elif wire == 2:
            length, offset = _varint(data, offset)
            end = offset + length
            value = data[offset:end]
            offset = end
        else:
            raise ValueError("unsupported protobuf wire type")
        if offset > len(data):
            raise ValueError("truncated protobuf field")
        result.append((number, value))
    return result


def fields(blob):
    result = {}
    for number, value in ordered_fields(blob):
        result.setdefault(number, []).append(value)
    return result


def single_text(payload, number):
    values = payload.get(number, [])
    if len(values) != 1 or not isinstance(values[0], bytes):
        return ""
    try:
        return values[0].decode("utf-8")
    except UnicodeDecodeError:
        return ""
