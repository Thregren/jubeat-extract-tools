#!/usr/bin/env python3
"""Check the explicit publishing allowlist, working files, and staged blobs."""
from pathlib import Path
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
PATTERNS = {
    'personal home path': re.compile(r'/(?:Users|home)/[^\s/]+|[A-Za-z]:[\\/]+Users[\\/]+', re.I),
    'credential': re.compile(r'gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|(?:AKIA|ASIA)[A-Z0-9]{16}|-----BEGIN (?:OPENSSH |RSA |EC )?PRIVATE KEY-----'),
    'email': re.compile(r'[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}'),
    'IP address': re.compile(r'(?<![\w.])(?:\d{1,3}\.){3}\d{1,3}(?![\w.])'),
}


def git(*args):
    return subprocess.run(['git', '-C', str(ROOT), *args], capture_output=True, check=True).stdout


def main():
    entries = (ROOT / 'PUBLISH_FILES.txt').read_text().splitlines()
    allowed = {line.strip() for line in entries if line.strip() and not line.startswith('#')}
    if len(allowed) != len([line for line in entries if line.strip() and not line.startswith('#')]):
        raise ValueError('Duplicate allowlist entries')
    errors = []
    indexed = set()
    if (ROOT / '.git').exists():
        visible = set(git('ls-files', '--cached', '--others', '--exclude-standard', '-z').decode().strip('\0').split('\0')) - {''}
        indexed = set(git('ls-files', '-z').decode().strip('\0').split('\0')) - {''}
        errors += [f'unlisted file: {name}' for name in sorted(visible - allowed)]
    for name in sorted(allowed):
        path = ROOT / name
        if path.is_symlink() or not path.is_file() or not path.resolve().is_relative_to(ROOT):
            errors.append(f'missing/unsafe file: {name}'); continue
        blobs = [('worktree', path.read_bytes())]
        if name in indexed:
            blobs.append(('index', git('show', ':' + name)))
        for origin, blob in blobs:
            if len(blob) > 256 * 1024 or b'\0' in blob:
                errors.append(f'binary/oversized {origin}: {name}'); continue
            try: text = blob.decode('utf-8')
            except UnicodeDecodeError:
                errors.append(f'non-text {origin}: {name}'); continue
            for label, pattern in PATTERNS.items():
                if pattern.search(text): errors.append(f'{label} in {origin}: {name}')
    if errors:
        print('\n'.join(errors), file=sys.stderr); return 1
    print(f'PASS: {len(allowed)} approved text files; no unexpected staged files, home paths, credentials, email addresses or IP addresses')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
