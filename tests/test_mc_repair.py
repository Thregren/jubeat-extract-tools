from pathlib import Path
import hashlib,json,sys,tempfile,unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from assets.eve import raw_events
from workflows.repair_mc import repair

class ExactAndRepairTests(unittest.TestCase):
    def test_guide_arguments_and_bad_lines_are_preserved(self):
        r=raw_events(b'10,HAKU,42,extra\n20,UNKNOWN,7\nbad,line\n')
        self.assertEqual(r['events'][0],{'tick':10,'command':'HAKU','arguments':['42','extra'],'value':42})
        self.assertEqual(r['events'][1]['command'],'UNKNOWN');self.assertEqual(r['unparsed_lines'],[{'line':3,'text':'bad,line'}])
    def test_mc_repaired_from_original_and_idempotent(self):
        raw=b'0,TEMPO,500000\n0,PLAY,0\n300,END,0\n'
        with tempfile.TemporaryDirectory() as d:
            v=Path(d);song=v/'songs/00000001';(song/'original').mkdir(parents=True)
            p=song/'original/bas.eve';p.write_bytes(raw);sha=hashlib.sha256(raw).hexdigest()
            row={'id':'00000001','members':[{'name':'bas.eve','sha256':sha}],'charts':{'BSC':{'sha256':sha,'mc_exported':True}}}
            (v/'manifest.json').write_text(json.dumps([row]));audio={'beat':[0,0,1],'sound':'bgm.wav','type':1}
            (song/'BSC.mc').write_text(json.dumps({'meta':{'mode':4},'time':[],'note':[audio,{'beat':[0,0,1],'index':15}]}))
            r=repair(v);self.assertFalse(r['errors']);self.assertEqual(len(r['changed']),1)
            mc=json.loads((song/'BSC.mc').read_text());self.assertEqual(mc['note'][0],audio);self.assertEqual(mc['note'][1]['index'],0)
            self.assertFalse(repair(v)['changed']);self.assertEqual(p.read_bytes(),raw)
if __name__=='__main__':unittest.main()
