"""Legacy asset preservation CLI; source game programs are never executed."""
import argparse
import json
from pathlib import Path
import sys
from .export import extract_pak, export_layers
from .pkfs import MAX_ENTRY_BYTES, path_hashes


def parser():
    p = argparse.ArgumentParser(description=__doc__)
    commands = p.add_subparsers(dest='command', required=True)
    pak = commands.add_parser('pak', help='Preserve PAK/index and export MD5-verified PKFS payloads')
    pak.add_argument('--input', type=Path, required=True, help='Resource root containing packinfo.bin')
    pak.add_argument('--output', type=Path, required=True)
    pak.add_argument('--candidates', type=Path, action='append', default=[], help='UTF-8 file; one exact virtual path per line; repeatable')
    pak.add_argument('--path-encoding', default='utf-8', help='Bytes used by the path hash; observed paths were ASCII')
    pak.add_argument('--native', choices=['auto', 'off', 'required'], default='auto')
    pak.add_argument('--max-entry-bytes', type=int, default=MAX_ENTRY_BYTES)
    export = commands.add_parser('export', help='Preserve loose/named IFS assets and select explicit resource layers')
    export.add_argument('--input', type=Path, help='Convenience input for a single base layer')
    export.add_argument('--layer', action='append', default=[], metavar='NAME=DIR', help='Ordered layer; later entries have higher priority')
    export.add_argument('--output', type=Path, required=True)
    export.add_argument('--skip-ifs', action='store_true', help='Retain raw IFS but do not export its members')
    export.add_argument('--decode-audio', action='store_true', help='Use optional assets.oki4s to output selected song WAVs')
    export.add_argument('--decode-textures', action='store_true', help='Use optional assets.texbin to export PXET texture PNGs')
    export.add_argument('--max-member-bytes', type=int, default=MAX_ENTRY_BYTES)
    hashes = commands.add_parser('hash', help='Calculate both path hashes; no dictionary is bundled')
    hashes.add_argument('paths', nargs='+')
    hashes.add_argument('--path-encoding', default='utf-8')
    return p


def main(argv=None):
    p = parser()
    args = p.parse_args(argv)
    try:
        if args.command == 'hash':
            rows = []
            for path in args.paths:
                crc, subkey = path_hashes(path, args.path_encoding)
                rows.append({'path': path, 'crc32': f'{crc:08x}', 'crc16_x25': f'{subkey:04x}'})
            print(json.dumps(rows, ensure_ascii=False, indent=2))
            return 0
        if args.command == 'pak':
            paths = []
            for file in args.candidates:
                paths += file.read_text(encoding='utf-8-sig').splitlines()
            report = extract_pak(args.input, args.output, paths, encoding=args.path_encoding,
                                 native=args.native, max_entry_bytes=args.max_entry_bytes)
        else:
            layers = [('base', args.input)] if args.input else []
            for spec in args.layer:
                name, sep, path = spec.partition('=')
                if not sep or not name or not path:
                    raise ValueError('--layer requires NAME=DIR')
                layers.append((name, Path(path)))
            report = export_layers(layers, args.output, extract_ifs=not args.skip_ifs,
                                   decode_audio=args.decode_audio, decode_textures=args.decode_textures,
                                   max_member_bytes=args.max_member_bytes)
        print(json.dumps(report['stats'], ensure_ascii=False, indent=2))
        return 0 if report['stats']['errors'] == 0 else 2
    except (OSError, ValueError, RuntimeError, LookupError) as exc:
        print(f'{type(exc).__name__}: {exc}', file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
