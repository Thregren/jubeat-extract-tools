"""CgBI (Apple 预乘 PNG) -> 标准 PNG 转换。纯标准库。

CgBI 差异：
  1. 签名后紧跟一个 CgBI 块（需丢弃）
  2. IDAT 是裸 deflate 流（无 zlib 头）
  3. 像素为 BGRA 且 alpha 预乘
"""
import struct, zlib

_SIG = b'\x89PNG\r\n\x1a\n'

def _paeth(a, b, c):
    p = a + b - c
    pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
    if pa <= pb and pa <= pc: return a
    if pb <= pc: return b
    return c

def _unfilter(raw, w, h, bpp):
    stride = w * bpp
    out = bytearray(h * stride)
    prev = bytearray(stride)
    pos = 0
    for y in range(h):
        ft = raw[pos]; pos += 1
        line = bytearray(raw[pos:pos + stride]); pos += stride
        if ft == 0:
            pass
        elif ft == 1:
            for i in range(bpp, stride):
                line[i] = (line[i] + line[i - bpp]) & 0xff
        elif ft == 2:
            for i in range(stride):
                line[i] = (line[i] + prev[i]) & 0xff
        elif ft == 3:
            for i in range(stride):
                a = line[i - bpp] if i >= bpp else 0
                line[i] = (line[i] + ((a + prev[i]) >> 1)) & 0xff
        elif ft == 4:
            for i in range(stride):
                a = line[i - bpp] if i >= bpp else 0
                c = prev[i - bpp] if i >= bpp else 0
                line[i] = (line[i] + _paeth(a, prev[i], c)) & 0xff
        else:
            raise ValueError('bad filter %d' % ft)
        out[y * stride:(y + 1) * stride] = line
        prev = line
    return out

def cgbi_to_png(data):
    """返回标准 PNG 字节。输入须为 CgBI PNG。"""
    if not data.startswith(_SIG):
        raise ValueError('not png')
    if len(data) > 64 * 1024 * 1024:
        raise ValueError('PNG exceeds 64 MiB input budget')
    pos = 8
    idat = bytearray()
    chunks = []          # (type, body) 保留的非关键块
    ihdr = None
    has_cgbi = False
    ended = False
    while pos + 8 <= len(data):
        (ln,) = struct.unpack_from('>I', data, pos)
        if pos + 12 + ln > len(data):
            raise ValueError('truncated PNG chunk')
        nm = data[pos + 4:pos + 8]
        body = data[pos + 8:pos + 8 + ln]
        pos += 12 + ln
        if nm == b'CgBI':
            has_cgbi = True; continue
        if nm == b'IHDR':
            ihdr = body; continue
        if nm == b'IDAT':
            idat += body; continue
        if nm == b'IEND':
            if ln != 0:
                raise ValueError('invalid IEND')
            ended = True
            break
        chunks.append((nm, body))
    if not ended:
        raise ValueError('missing IEND')
    if ihdr is None or len(ihdr) != 13:
        raise ValueError('no IHDR')
    if not has_cgbi:
        return data                     # 已是标准 PNG
    w, h, bit, color, comp, filt, inter = struct.unpack('>IIBBBBB', ihdr)
    if bit != 8 or color != 6 or inter != 0 or comp != 0 or filt != 0:
        raise ValueError('unsupported CgBI %dx%d bit=%d color=%d' % (w, h, bit, color))
    if w < 1 or h < 1 or w * h > 16 * 1024 * 1024:
        raise ValueError('invalid dimensions or image exceeds pixel budget')
    stride = w * 4
    expected = h * (stride + 1)
    decoder = zlib.decompressobj(-15)
    raw = decoder.decompress(bytes(idat), expected + 1)
    if len(raw) != expected or not decoder.eof or decoder.unused_data:
        raise ValueError('IDAT length does not match image dimensions')
    px = _unfilter(raw, w, h, 4)
    # BGRA 预乘 -> RGBA 直通
    for i in range(0, w * h * 4, 4):
        b, g, r, a = px[i], px[i + 1], px[i + 2], px[i + 3]
        if a != 255:
            if a == 0:
                b = g = r = 0
            else:
                b = min(255, (b * 255 + a // 2) // a)
                g = min(255, (g * 255 + a // 2) // a)
                r = min(255, (r * 255 + a // 2) // a)
        px[i], px[i + 1], px[i + 2], px[i + 3] = r, g, b, a
    out = bytearray(_SIG)
    hdr_filtered = bytearray()
    for y in range(h):
        hdr_filtered.append(0)
        hdr_filtered += px[y * stride:(y + 1) * stride]
    def chunk(nm, body):
        return struct.pack('>I', len(body)) + nm + body + struct.pack('>I', zlib.crc32(nm + body) & 0xffffffff)
    out += chunk(b'IHDR', ihdr)
    for nm, body in chunks:
        if nm in (b'iCCP', b'sRGB', b'gAMA', b'cHRM', b'pHYs', b'tEXt', b'tIME'):
            out += chunk(nm, body)
    out += chunk(b'IDAT', zlib.compress(bytes(hdr_filtered), 9))
    out += chunk(b'IEND', b'')
    return bytes(out)
