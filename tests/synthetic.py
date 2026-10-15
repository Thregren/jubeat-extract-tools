"""Artificial fixtures created in memory; no game data or extracted bytes."""
from functools import lru_cache
from pathlib import Path
import io
import plistlib
import re
import struct
import zipfile
import zlib

MASK = 0xffffffff

@lru_cache(maxsize=1)
def cipher():
    source = (Path(__file__).resolve().parents[1] / 'mobile/tools/jbf.c').read_text()
    tables = []
    for name in ['BF_P', 'BF_S0', 'BF_S1', 'BF_S2', 'BF_S3']:
        block = re.search(r'\b' + name + r'\[\d+\]\s*=\s*\{([^}]+)', source).group(1)
        tables.append([int(v, 16) for v in re.findall(r'0x([0-9a-f]+)u', block)])
    p, s = tables[0], tables[1:]
    key = bytes.fromhex('f9a142c70b07d9a8093b56b8c2eeb698')
    for i in range(18):
        p[i] ^= int.from_bytes(bytes(key[(4 * i + j) % len(key)] for j in range(4)), 'big')
    def encrypt(left, right):
        for i in range(16):
            left ^= p[i]
            f = ((s[0][left >> 24] + s[1][(left >> 16) & 255]) & MASK) ^ ((s[2][(left >> 8) & 255] + s[3][left & 255]) & MASK)
            right ^= f
            left, right = right, left
        left, right = right, left
        return left ^ p[17], right ^ p[16]
    left = right = 0
    for table in [p, *s]:
        for i in range(0, len(table), 2):
            left, right = encrypt(left, right)
            table[i:i + 2] = [left, right]
    return encrypt


def encrypted(plain):
    encode = cipher()
    padded = plain + bytes((-len(plain)) % 8)
    prev = bytes.fromhex('e36631da2c85a064')
    out = bytearray()
    for start in range(0, len(padded), 8):
        block = bytes(a ^ b for a, b in zip(padded[start:start + 8], prev))
        prev = struct.pack('>II', *encode(*struct.unpack('>II', block)))
        out.extend(prev)
    return bytes(out) + struct.pack('>II', len(plain), len(out))


def sequence(records=None):
    if records is None:
        records = [(5, 0, 500000), (1, 0, 0), (6, 300, (300 << 8) | (1 << 6) | (1 << 4)), (5, 600, 1000000), (1, 900, 2), (2, 1200, 0)]
    header = bytearray(0x60)
    header[:4] = b'IJBQ'
    struct.pack_into('<III', header, 4, len(records), sum(t in (1, 6) for t, _, _ in records), 1200)
    return bytes(header) + b''.join(struct.pack('<II', t | (tick << 8), value) for t, tick, value in records)


def chunk(name, body):
    return struct.pack('>I', len(body)) + name + body + struct.pack('>I', zlib.crc32(name + body))


def cgbi(raw=None, width=1, height=1):
    raw = raw if raw is not None else bytes([0, 25, 50, 100, 128])
    compressor = zlib.compressobj(wbits=-15)
    return b'\x89PNG\r\n\x1a\n' + chunk(b'CgBI', bytes(4)) + chunk(b'IHDR', struct.pack('>IIBBBBB', width, height, 8, 6, 0, 0, 0)) + chunk(b'IDAT', compressor.compress(raw) + compressor.flush()) + chunk(b'IEND', b'')


def song_zip(extra=None):
    meta = {'Name': 'Synthetic Song', 'Artist': 'Synthetic Artist', 'LvBas': 1, 'LvAdv': 4, 'LvExt': 7}
    entries = {'infov2': plistlib.dumps(meta, fmt=plistlib.FMT_BINARY), 'artwork': cgbi(), 'bgm': b'synthetic audio bytes', 'index': b'synthetic preview bytes'}
    entries.update({'seq_' + c: sequence() for c in ('bas', 'adv', 'ext')})
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w') as z:
        for name, blob in entries.items(): z.writestr(name, encrypted(blob))
        for name, blob in (extra or {}).items(): z.writestr(name, blob)
    return buffer.getvalue()


def backup(path, bad=False):
    with zipfile.ZipFile(path, 'w') as z:
        z.writestr('example.app/Documents/00000001.jbt', b'broken' if bad else song_zip())
        z.writestr('example.app/Documents/private-settings.txt', 'not a song; never extracted')


def ifs_bytes(chart=None, preview=b''):
    if chart is None:
        chart = ('0,TEMPO,500000\n0,PLAY,0\n300,LONG,76880\n600,TEMPO,1000000\n900,PLAY,2\n1200,END,0\n').encode('cp932')
    audio = bytearray(0x20)
    audio[:4] = b'BMP\0'
    struct.pack_into('>I', audio, 4, 16)
    audio[16] = 2
    struct.pack_into('>I', audio, 20, 8000)
    blocks = [bytes(audio) + bytes(range(16)), preview, chart, chart, chart]
    header = bytearray(0x80)
    struct.pack_into('>I', header, 0x10, len(header))
    offset = 0
    for i, data in enumerate(blocks):
        struct.pack_into('>III', header, len(header) - 60 + 12 * i, offset, len(data), 0)
        offset += len(data)
    return bytes(header) + b''.join(blocks)
