from pathlib import Path
import argparse, os, shutil

def configure(description):
    p=argparse.ArgumentParser(description=description)
    p.add_argument('--root',type=Path,required=True,help='导出根目录')
    p.add_argument('--source',type=Path,help='预先解压的资源树；仅可与一个版本名称合用')
    p.add_argument('versions',nargs='+',help='本地输出版本目录名称，不含路径分隔符')
    args=p.parse_args();root=args.root.resolve();root.mkdir(parents=True,exist_ok=True)
    for name in args.versions:
        if name in ('.','..') or Path(name).name!=name or '/' in name or '\\' in name:
            p.error('版本名称必须是单个目录名称')
        version=root/name
        if version.is_symlink() or (version.exists() and root not in version.resolve().parents):
            p.error('版本输出目录不能是符号链接或逃出输出根目录')
        if version.exists():
            pending=[version]
            while pending:
                with os.scandir(pending.pop()) as entries:
                    for entry in entries:
                        if entry.is_symlink():p.error('已有导出树含符号链接，请使用独立的真实目录')
                        if entry.is_dir(follow_symlinks=False):pending.append(Path(entry.path))
    if args.source:
        if len(args.versions)!=1:p.error('--source 仅支持一个版本')
        source=args.source.resolve();target=root/args.versions[0]/'raw'
        if not source.is_dir():p.error('--source 不是目录')
        if source==root or source in root.parents or root in source.parents:
            p.error('输入与输出树必须分开')
        if target.exists():p.error('raw 已存在，请省略 --source 复用已有输入或使用新的输出目录')
        for base,dirs,files in os.walk(source):
            base=Path(base);dest=target/base.relative_to(source);dest.mkdir(parents=True,exist_ok=True)
            for name in dirs+files:
                if (base/name).is_symlink():raise ValueError('输入树不接受符号链接')
            for name in files:
                src=base/name;dst=dest/name
                try:os.link(src,dst)
                except OSError:shutil.copy2(src,dst)
    return root,args.versions
