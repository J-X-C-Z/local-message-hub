"""全量解密器：拿到 K16（或 ct2 页密钥 K）后，解密所有库 + 合并 WAL。

用法:
  python decrypt_all.py ct1 <K16_hex>          # AES 每页派生族
  python decrypt_all.py ct2 <K_hex>            # SM4 单密钥族
输出: 解密结果/<库名>/<相对路径>.db
"""
import os, sys, glob, struct, hashlib, shutil
from .config import load_local_config, local_data_dir

HERE = os.path.dirname(os.path.abspath(__file__))
from Crypto.Cipher import AES
from .sm4_scalar_reference import expand, decrypt as sm4_dec

MAGIC = b'SQLite format 3\x00'
PAGE_CONST = bytes.fromhex('1000020200402020')
OUT = os.path.join(local_data_dir(), 'decrypted')
PS = 4096


def lcg16(pgno):
    n = pgno + 1
    out = bytearray()
    for _ in range(4):
        q, r = divmod(n, 0xCE26)
        n = r * 0x9EF4 - q * 0xECF
        if n < 0:
            n += 0x7FFFFF07
        out += (n & 0xFFFFFFFF).to_bytes(4, 'little')
    return bytes(out)


def strong_ok(pt, ps=PS):
    if len(pt) < 100:
        return False
    if pt[0] not in (2, 5, 10, 13):
        return False
    ncell = (pt[3] << 8) | pt[4]
    cs = (pt[5] << 8) | pt[6]
    if not (1 <= ncell <= 900):
        return False
    if not (8 + 2 * ncell <= cs < ps):
        return False
    for i in range(ncell):
        o = (pt[8 + 2*i] << 8) | pt[9 + 2*i]
        if o < cs or o >= ps:
            return False
    return True


def sm4_cbc(key16, iv16, data):
    rk = expand(list(struct.unpack('>4I', key16)))
    prev = iv16
    out = bytearray()
    for i in range(0, len(data) - len(data) % 16, 16):
        blk = data[i:i+16]
        pt = struct.pack('>4I', *sm4_dec(rk, list(struct.unpack('>4I', blk))))
        out += bytes(a ^ b for a, b in zip(pt, prev))
        prev = blk
    return bytes(out)


def dec_page_ct1(k16, pgno, raw):
    K = hashlib.md5(k16 + struct.pack('<I', pgno) + b'sAlT').digest()
    V = hashlib.md5(lcg16(pgno)).digest()
    if pgno == 1:
        return MAGIC + AES.new(K, AES.MODE_CBC, V).decrypt(raw[8:16] + raw[24:PS])
    return AES.new(K, AES.MODE_CBC, V).decrypt(raw[:PS])


def dec_page_ct2(k, pgno, raw):
    if pgno == 1:
        tmp = bytearray(raw)
        tmp[16:24] = raw[8:16]
        body = sm4_cbc(k, k, bytes(tmp[16:PS]))
        page = bytearray(tmp[:16]) + bytearray(body)
        page[0:16] = MAGIC
        return bytes(page)
    return sm4_cbc(k, k, raw[:PS])


def parse_wal_frames(wpath):
    try:
        w = open(wpath, 'rb').read()
    except OSError:
        return []
    if len(w) < 32 or w[0:4] not in (b'\x37\x7f\x06\x82', b'\x37\x7f\x06\x83'):
        return []
    s1 = w[16:20]; s2 = w[20:24]
    frames = []
    i = 32
    while i + 24 + PS <= len(w):
        hdr = w[i:i+24]
        pgno = int.from_bytes(hdr[4:8], 'big')
        dbsize = int.from_bytes(hdr[8:12], 'big')
        if hdr[12:16] != s1 or hdr[16:20] != s2:
            break
        frames.append((pgno, dbsize, w[i+24:i+24+PS]))
        i += 24 + PS
    return frames


def decrypt_db(path, mode, key):
    disk = open(path, 'rb').read()
    if disk[:16] == MAGIC:
        return disk, 0, 'plain'
    npg = len(disk) // PS
    if npg == 0:
        return None, 0, 'empty'
    dec = dec_page_ct1 if mode == 'ct1' else dec_page_ct2
    out = bytearray()
    good = 0
    for pg in range(1, npg + 1):
        raw = disk[(pg-1)*PS: pg*PS]
        try:
            pt = dec(key, pg, raw)
        except Exception:
            pt = raw
        if len(pt) < PS:
            pt = pt + bytes(PS - len(pt))
        out += pt[:PS]
        if strong_ok(pt):
            good += 1
    # WAL
    wpath = path + '-wal'
    nframes = 0
    if os.path.exists(wpath):
        frames = parse_wal_frames(wpath)
        if frames:
            last = max(i for i, f in enumerate(frames) if f[1] > 0) if any(f[1] > 0 for f in frames) else len(frames) - 1
            for idx, (pgno, dbsize, praw) in enumerate(frames[:last+1]):
                try:
                    pt = dec(key, pgno, praw)
                except Exception:
                    continue
                off = (pgno - 1) * PS
                if off + PS <= len(out):
                    out[off:off+PS] = pt[:PS]
                elif off < len(out):
                    out[off:] = pt[:len(out)-off]
                nframes += 1
            dsz = frames[last][1]
            if dsz > 0 and dsz * PS <= len(out):
                out = out[:dsz*PS]
    return bytes(out), good, f'{good}/{npg}页 +{nframes}WAL帧'


def main():
    raise SystemExit("Use WeComAdapter.refresh() to process configured local snapshots.")


if __name__ == '__main__':
    main()
