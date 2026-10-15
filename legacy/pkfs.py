"""Bounded PKFS index parsing, candidate-path verification and MD5-gated decoding."""
from __future__ import annotations
import ctypes
import hashlib
import os
from pathlib import Path
import platform
import shlex
import shutil
import struct
import subprocess
import sys
import zlib

ENTRY = struct.Struct('<16sIHHII')
MAX_ENTRY_BYTES = 512 * 1024 * 1024


def crc16_x25(data: bytes) -> int:
    value = 0xffff
    for byte in data:
        value ^= byte
        for _ in range(8):
            value = (value >> 1) ^ (0x8408 if value & 1 else 0)
    return value ^ 0xffff


def path_hashes(name: str, encoding: str = 'utf-8') -> tuple[int, int]:
    raw = name.encode(encoding, errors='strict')
    return zlib.crc32(raw), crc16_x25(raw)


def parse_index(blob: bytes) -> list[dict]:
    if len(blob) < 16:
        raise ValueError('packinfo header is shorter than 16 bytes')
    table_bytes = struct.unpack_from('<I', blob, 8)[0]
    if table_bytes % ENTRY.size or table_bytes > len(blob) - 16:
        raise ValueError('packinfo table length is unaligned or out of bounds')
    rows = []
    for number in range(table_bytes // ENTRY.size):
        md5, crc, subkey, pack, offset, size = ENTRY.unpack_from(blob, 16 + number * ENTRY.size)
        rows.append({'record': number, 'stored_md5': md5.hex(), 'crc32': crc,
                     'crc16': subkey, 'pack': pack, 'offset': offset, 'size': size})
    return rows


def xor_python(data: bytes, crc: int, subkey: int, historical_tail: bool = False) -> bytes:
    out = bytearray(data)
    def rol(value):
        value &= 0xffffffff
        return ((value << 11) | (value >> 21)) & 0xffffffff
    key = rol(crc + subkey)
    end = len(out) // 4 * 4
    for pos in range(0, end, 4):
        value = int.from_bytes(out[pos:pos + 4], 'little') ^ key
        out[pos:pos + 4] = value.to_bytes(4, 'little')
        key = rol(key + subkey)
    for j in range(len(out) - end):
        out[end if historical_tail else end + j] ^= (key >> (8 * j)) & 255
    return bytes(out)


class XorCodec:
    """Optional local C acceleration; pure Python is the complete fallback.

    Compiled files are built under the repository's ignored .build directory.
    No precompiled binary is shipped. mode='required' exposes build failures.
    """
    def __init__(self, mode='auto'):
        if mode not in {'auto', 'off', 'required'}:
            raise ValueError('native mode must be auto, off or required')
        self.native = None
        self.warning = None
        if mode == 'off':
            return
        try:
            source = Path(__file__).with_name('pkfs.c')
            tag = hashlib.sha256(source.read_bytes()).hexdigest()[:16]
            suffix = '.dylib' if sys.platform == 'darwin' else '.so'
            build = source.parent.parent / '.build'
            build.mkdir(exist_ok=True)
            output = build / f'legacy-pkfs-{platform.system()}-{platform.machine()}-{tag}{suffix}'
            if not output.exists():
                compiler = shlex.split(os.environ.get('CC', 'cc'))
                if not compiler or not shutil.which(compiler[0]):
                    raise RuntimeError('C compiler unavailable; install one or use --native off')
                temporary = output.with_name(output.name + f'.{os.getpid()}.tmp')
                command = compiler + ['-O3', '-std=c99']
                command += ['-dynamiclib'] if sys.platform == 'darwin' else ['-shared', '-fPIC']
                try:
                    subprocess.run(command + [str(source), '-o', str(temporary)], check=True,
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                    temporary.replace(output)
                finally:
                    temporary.unlink(missing_ok=True)
            lib = ctypes.CDLL(str(output))
            fn = lib.legacy_pkfs_xor
            fn.argtypes = [ctypes.c_void_p, ctypes.c_size_t, ctypes.c_uint32,
                           ctypes.c_uint16, ctypes.c_int]
            fn.restype = None
            self.native = fn
        except (OSError, RuntimeError, subprocess.CalledProcessError) as exc:
            if mode == 'required':
                raise RuntimeError(f'Native PKFS initialization failed: {exc}') from exc
            self.warning = f'Native acceleration unavailable; using Python: {type(exc).__name__}'

    def __call__(self, data, crc, subkey, historical_tail=False):
        if self.native is None:
            return xor_python(data, crc, subkey, historical_tail)
        buf = ctypes.create_string_buffer(data, len(data) + 1)
        self.native(buf, len(data), crc, subkey, int(historical_tail))
        return buf.raw[:len(data)]


def decode_payload(data: bytes, row: dict, codec=None) -> tuple[bytes, str, bool]:
    """Only return transformed bytes when the stored MD5 confirms them.

    The historical remainder operation XORs all key remainder bytes into the
    *first* remaining data byte. It is not padding and cannot be 'fixed' by
    stripping or appending bytes. A failed match keeps the original stored slice.
    """
    if hashlib.md5(data).hexdigest() == row['stored_md5']:
        return data, 'verbatim', True
    codec = codec or xor_python
    modes = [('pkfs_rolling_xor', False), ('pkfs_rolling_xor_historical_tail', True)]
    for mode, historical in modes:
        if historical and len(data) % 4 < 2:
            continue  # remainder 0 or 1 is identical to the conventional operation
        decoded = codec(data, row['crc32'], row['crc16'], historical)
        if hashlib.md5(decoded).hexdigest() == row['stored_md5']:
            return decoded, mode, True
    return data, 'stored_unverified', False


def candidate_index(paths, encoding='utf-8') -> dict:
    out = {}
    for name in paths:
        if not name:
            continue
        key = path_hashes(name, encoding)
        out.setdefault(key, set()).add(name)
    return {key: sorted(value) for key, value in out.items()}
