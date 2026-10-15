from pathlib import Path

def new_directory(output):
    out=Path(output)
    if out.is_symlink() or (out.exists() and (not out.is_dir() or any(out.iterdir()))):
        raise FileExistsError('Use a new or empty output directory')
    out.mkdir(parents=True,exist_ok=True)
    return out
