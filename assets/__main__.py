"""单文件资源导出入口。python -m assets --help"""
from pathlib import Path
import argparse,json,shutil

def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('format',choices=['bmp','texbin','texture','va3','s3p','ifs','eve'])
    p.add_argument('source',type=Path);p.add_argument('output',type=Path)
    p.add_argument('--no-tdxt',action='store_true',help='texbin不另存原始TDXT')
    p.add_argument('--decode',action='store_true',help='S3P额外用ffmpeg解为FLAC')
    p.add_argument('--code',choices=['BSC','ADV','EXT'],default='EXT',help='EVE的难度身份')
    a=p.parse_args(argv)
    if a.output.exists():p.error('输出路径已存在，请使用新路径，避免覆盖已有结果')
    if a.format=='bmp':
        from .oki4s import bmp_to_wav
        result=bmp_to_wav(a.source.read_bytes(),a.output)
    elif a.format=='texbin':
        from .texbin import extract_texbin
        result=extract_texbin(a.source,a.output,keep_tdxt=not a.no_tdxt)
    elif a.format=='texture':
        from .individual_tex import extract_individual_tex
        result=extract_individual_tex(a.source,a.output)
    elif a.format=='va3':
        from .va3 import extract_va3
        result=extract_va3(a.source,a.output)
    elif a.format=='s3p':
        from .s3p import extract_s3p
        result=extract_s3p(a.source,a.output,decode=a.decode)
    elif a.format=='ifs':
        from .ifs import extract_ifs
        result=extract_ifs(a.source,a.output)
    else:
        from arcade.import_ifs import _parse_events,beat_tuple
        from .eve import parse_eve_chart,raw_events
        raw=a.source.read_bytes();result=raw_events(raw)
        a.output.mkdir(parents=True);shutil.copy2(a.source,a.output/'original.eve')
        (a.output/'events.json').write_text(json.dumps(result,ensure_ascii=False,indent=2))
        if result['unparsed_lines']:
            print(json.dumps(result,ensure_ascii=False,indent=2));return 2
        notes,timing,_=parse_eve_chart(raw,a.code)
        mc={'meta':{'mode':4,'version':a.code,'song':{'title':a.source.stem}},'time':[{'beat':beat_tuple(b),'bpm':float(v)} for b,v in timing],'note':[]}
        for beat,index,endbeat,endindex in notes:
            n={'beat':beat_tuple(beat),'index':index}
            if endbeat is not None:n.update(endbeat=beat_tuple(endbeat),endindex=endindex)
            mc['note'].append(n)
        (a.output/(a.code+'.mc')).write_text(json.dumps(mc,ensure_ascii=False,indent=2))
    print(json.dumps(result,ensure_ascii=False,indent=2))
    return 2 if result.get('errors') else 0
if __name__=='__main__':raise SystemExit(main())
