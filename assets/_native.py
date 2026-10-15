"""Optional, local-only native acceleration; never downloads a binary."""
from pathlib import Path
import ctypes, hashlib, os, platform, shutil, subprocess, sys, threading

def load_library(source):
    if sys.byteorder != 'little' or sys.platform not in ('darwin', 'linux'):
        raise RuntimeError('Native acceleration is verified on little-endian macOS/Linux only')
    src = Path(__file__).with_name(source)
    key = hashlib.sha256(src.read_bytes() + platform.machine().encode()).hexdigest()[:16]
    root = Path(os.environ.get('JUBEAT_BUILD_DIR', Path(__file__).resolve().parents[1] / '.build'))
    root.mkdir(parents=True, exist_ok=True)
    suffix = '.dylib' if sys.platform == 'darwin' else '.so'
    target = root / (src.stem + '-' + key + suffix)
    if not target.exists():
        cc = shutil.which(os.environ.get('CC', 'cc'))
        if not cc:
            raise RuntimeError('No C compiler; use Python fallback')
        tmp = target.with_name(target.name + f'.{os.getpid()}.{threading.get_ident()}.tmp')
        flags = ['-dynamiclib'] if sys.platform == 'darwin' else ['-shared', '-fPIC']
        try:
            proc = subprocess.run([cc, '-O3', *flags, str(src), '-o', str(tmp)], capture_output=True)
            if proc.returncode:
                raise RuntimeError('Native build failed: ' + proc.stderr.decode(errors='replace'))
            tmp.replace(target)
        finally:
            tmp.unlink(missing_ok=True)
    return ctypes.CDLL(str(target))
