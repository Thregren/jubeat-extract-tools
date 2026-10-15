"""Artificial bytes only: codecs, bounds, exact ticks and update replacement."""
from pathlib import Path
import importlib.util,json,random,struct,sys,tempfile,unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from arcade import import_ifs
from assets import oki4s
from assets.eve import parse_eve_chart
from assets.s3p import extract_s3p
import synthetic
HAS_PIL=importlib.util.find_spec('PIL') is not None

def bmp(ch=2,payload=b'\x12\x34',count=0):
    h=bytearray(32);h[:4]=b'BMP\0';struct.pack_into('>I',h,4,count);h[16]=ch;struct.pack_into('>I',h,20,8000)
    return bytes(h)+payload

def texbin_fixture(pixels,width,height,fmt=0x10,endian='<',rectangle=False):
    """Artificial TEXP with one TDXT and optional right-column TCER region."""
    def names(label):
        text=label.encode()+b'\0';data=bytearray(40+len(text));data[:4]=b'NAMP' if endian=='<' else b'PMAN'
        struct.pack_into(endian+'I',data,16,1);struct.pack_into(endian+'I',data,24,28)
        struct.pack_into(endian+'III',data,28,0,0,40);data[40:]=text
        return bytes(data)+bytes((-len(data))%4)
    tdxt=struct.pack(endian+'4sIIIHHIII',b'TDXT' if endian=='<' else b'TXDT',0,0,64+len(pixels),width,height,fmt,0,0)
    tdxt+=bytes(12)+struct.pack(endian+'I',3)+bytes(16)+pixels
    labels=names('atlas');rect=bytes();rectoff=0
    if rectangle:
        rectoff=64+len(labels);rnames=names('right_column');header=bytearray(28);header[:4]=b'TCER' if endian=='<' else b'RECT'
        struct.pack_into(endian+'III',header,16,1,28,28+len(rnames))
        rect=bytes(header)+rnames+struct.pack(endian+'IHHHH',0,1,2,0,2)
    entryoff=64+len(labels)+len(rect);payload=struct.pack('>II',len(tdxt),0)+tdxt
    header=bytearray(64);header[:4]=b'PXET' if endian=='<' else b'TEXP'
    for off,value in [(12,entryoff+12+len(payload)),(20,1),(32,rectoff),(52,64),(60,entryoff)]:struct.pack_into(endian+'I',header,off,value)
    return bytes(header)+labels+rect+struct.pack(endian+'III',0,len(payload),entryoff+12)+payload,tdxt

class AudioAndChartTests(unittest.TestCase):
    def test_native_and_fallback_equal_reference(self):
        rng=random.Random(37)
        for channels in (1,2):
            for count in (0,1,13,10000):
                raw=bmp(channels,bytes(rng.randrange(256) for _ in range(51)),count)
                expected=import_ifs.decode_bmp_to_pcm(raw)[0]
                self.assertEqual(oki4s.decode_bmp(raw)[0],expected)
                with patch.object(oki4s,'_LIB',None):self.assertEqual(oki4s.decode_bmp(raw)[0],expected)
    def test_bmp_reject_bad_and_empty(self):
        for raw in (b'BMP',bmp(3),bmp(1,b'')):
            with self.assertRaises(ValueError):oki4s.decode_bmp(raw)
    def test_named_eve_adapter_uses_same_timing(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'example.ifs';p.write_bytes(synthetic.ifs_bytes())
            raw=import_ifs.read_block(p,4)
            self.assertEqual(parse_eve_chart(raw,'EXT'),import_ifs.parse_chart(p,'EXT'))
    def test_s3p_original_payload_and_bounds(self):
        member=b'S3V0'+struct.pack('<II',12,4)+b'FAKE'
        archive=b'S3P0'+struct.pack('<III',1,16,len(member))+member
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'bank';p.write_bytes(archive);r=extract_s3p(p,Path(d)/'out')
            self.assertFalse(r['errors']);self.assertEqual((Path(d)/'out/0000.asf').read_bytes(),b'FAKE')
            p.write_bytes(archive[:-1]);self.assertTrue(extract_s3p(p,Path(d)/'bad')['errors'])

@unittest.skipUnless(HAS_PIL,'optional Pillow required for full assets')
class ImageAndBankTests(unittest.TestCase):
    def test_gfgl_pixels_and_bad_enums(self):
        from assets.individual_tex import decode_individual
        header=b'GFGLTI2D20140326'+struct.pack('<9I',3553,0,6408,1,1,0,6408,5121,4)
        image,_=decode_individual(header+b'\x01\x02\x03\xff');self.assertEqual(image.getpixel((0,0)),(1,2,3,255))
        bad=bytearray(header+b'\0'*4);struct.pack_into('<I',bad,16+7*4,999)
        with self.assertRaises(ValueError):decode_individual(bytes(bad))
    def test_texbin_rgba_and_lzss(self):
        from assets.texbin import extract_texbin,lzss
        tdxt=struct.pack('<4sIIIHHIII',b'TDXT',0,0,72,2,1,0x10,0,0)+bytes(12)+struct.pack('<I',3)+bytes(16)+bytes([231,67,19,129,11,101,203,47])
        names=bytearray(48);names[:4]=b'NAMP';struct.pack_into('<I',names,16,1);struct.pack_into('<I',names,24,28);struct.pack_into('<III',names,28,0,0,40);names[40:44]=b'pix\0'
        h=bytearray(64);h[:4]=b'PXET';struct.pack_into('<I',h,20,1);struct.pack_into('<I',h,52,64);struct.pack_into('<I',h,60,112)
        blob=bytes(h)+bytes(names)+struct.pack('<III',0,8+len(tdxt),124)+struct.pack('>II',len(tdxt),0)+tdxt
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'texture';p.write_bytes(blob);r=extract_texbin(p,Path(d)/'out');self.assertFalse(r['errors'])
            from PIL import Image
            with Image.open(Path(d)/'out/pix.png') as im:self.assertEqual(list(im.getdata()),[(231,67,19,129),(11,101,203,47)])
        self.assertEqual(lzss(struct.pack('>II',3,4)+b'\x07abc'),b'abc')
        with self.assertRaises(ValueError):lzss(struct.pack('>II',100,1)+b'\x01')
    def test_texbin_fmt10_rgba_tcer_and_standalone_default(self):
        from assets.texbin import extract_texbin
        from assets.vendor.tdxt import TDXT
        from PIL import Image
        pixels=[(201,51,7,255),(13,31,223,117),(17,199,53,67),(99,11,173,239)]
        for endian in ('<','>'):
            with self.subTest(endian=endian),tempfile.TemporaryDirectory() as temporary:
                blob,tdxt=texbin_fixture(bytes(v for p in pixels for v in p),2,2,endian=endian,rectangle=True)
                source=Path(temporary)/'source';source.write_bytes(blob);output=Path(temporary)/'output'
                result=extract_texbin(source,output);self.assertFalse(result['errors'])
                self.assertEqual(result['pixel_decode_policy'],'texp-0x10-rgba-v1')
                self.assertEqual(json.loads((output/'texture_manifest.json').read_text())['pixel_decode_policy'],'texp-0x10-rgba-v1')
                with Image.open(output/'atlas.png') as image:self.assertEqual(list(image.getdata()),pixels)
                with Image.open(output/'right_column.png') as image:
                    self.assertEqual(image.size,(1,2));self.assertEqual(list(image.getdata()),[pixels[1],pixels[3]])
                self.assertEqual((output/'atlas.tdxt').read_bytes(),tdxt)
                # The correction belongs only to TEXP; standalone vendor semantics stay BGRA.
                self.assertEqual(TDXT.fromBytes(tdxt).img.getpixel((0,0)),(7,51,201,255))
    def test_texbin_other_pixel_formats_keep_their_channel_order(self):
        from assets.texbin import extract_texbin
        from PIL import Image
        for fmt,raw,expected in [(0x0e,bytes([231,67,19]),(231,67,19)),(0x0b,b'\0\xf8',(255,0,0))]:
            with self.subTest(fmt=fmt),tempfile.TemporaryDirectory() as temporary:
                blob,_=texbin_fixture(raw,1,1,fmt=fmt);source=Path(temporary)/'source';source.write_bytes(blob)
                output=Path(temporary)/'output';result=extract_texbin(source,output);self.assertFalse(result['errors'])
                with Image.open(output/'atlas.png') as image:self.assertEqual(image.getpixel((0,0)),expected)
    def test_va3_metadata_and_unsupported_variant(self):
        from assets.va3 import extract_va3
        h=b'VA3W'+struct.pack('<BBBBIIIII',1,0,0,2,1,4,28,32,96)
        e=bytearray(64);struct.pack_into('<IIHHIIIBBHHH',e,0,0,2,1,16,8000,0,0,70,64,3,0,0);e[32:37]=b'test\0'
        blob=h+b'GDXH'+bytes(e)+b'\x12\x34'
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'bank';p.write_bytes(blob);r=extract_va3(p,Path(d)/'out');self.assertFalse(r['errors']);self.assertEqual(r['records'][0]['volume'],70)
            bad=bytearray(blob);bad[7]=3;p.write_bytes(bad)
            with self.assertRaises(ValueError):extract_va3(p,Path(d)/'bad')

if __name__=='__main__':unittest.main()
