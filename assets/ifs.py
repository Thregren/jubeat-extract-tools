"""General named IFS member preservation with checked output paths."""
from pathlib import Path, PurePosixPath
import hashlib,json

def extract_ifs(source,output):
    from ifstools.ifs import IFS
    source=Path(source);out=Path(output)
    if out.is_symlink() or (out.exists() and any(out.iterdir())):raise FileExistsError('Use a new or empty output directory')
    out.mkdir(parents=True,exist_ok=True);members=out/'original';members.mkdir();obj=IFS(str(source));records=[]
    try:
        for f in obj.tree.all_files:
            name=f.full_path.replace('\\','/');rel=PurePosixPath(name)
            if rel.is_absolute() or '..' in rel.parts or not rel.parts or ':' in rel.parts[0]:raise ValueError('Unsafe IFS member path')
            dest=members.joinpath(*rel.parts)
            if members.resolve() not in dest.resolve().parents:raise ValueError('IFS member outside output')
            if dest.exists():raise FileExistsError(str(dest))
            raw=f.load(convert_kbin=False);dest.parent.mkdir(parents=True,exist_ok=True);dest.write_bytes(raw)
            records.append({'name':name,'bytes':len(raw),'sha256':hashlib.sha256(raw).hexdigest()})
        (out/'ifs_manifest.xml').write_text(obj.manifest.to_text())
    finally:obj.close()
    result={'source':str(source),'members':records,'errors':[]}
    (out/'members.json').write_text(json.dumps(result,ensure_ascii=False,indent=2));return result
