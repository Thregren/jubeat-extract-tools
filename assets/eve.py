"""Reuse the published converter's EVE timing logic with named raw members.
The in-memory carrier is only an API adapter; it is never published as game data.
"""
from types import SimpleNamespace
import struct
from arcade import import_ifs
def raw_events(raw):
 """Exact integer ticks, command spelling/order and every argument, including guide values."""
 try:text=raw.decode('utf-8-sig');encoding='utf-8-sig'
 except UnicodeDecodeError:text=raw.decode('cp932');encoding='cp932'
 events=[];unparsed=[]
 for number,line in enumerate(text.splitlines(),1):
  if not line.strip():continue
  fields=[f.strip() for f in line.split(',')]
  try:
   if len(fields)<2 or not fields[1]:raise ValueError('missing command')
   event={'tick':int(fields[0]),'command':fields[1],'arguments':fields[2:]}
   if len(fields)>2:
    try:event['value']=int(fields[2])
    except ValueError:pass
   events.append(event)
  except ValueError:unparsed.append({'line':number,'text':line})
 return {'tick_hz':300,'encoding':encoding,'events':events,'unparsed_lines':unparsed}
class _EveCarrier:
 def __init__(self,raw,name):
  blocks=[b'',b'',raw,raw,raw];header=bytearray(128);struct.pack_into('>I',header,16,128);off=0
  for n,b in enumerate(blocks):struct.pack_into('>III',header,68+12*n,off,len(b),0);off+=len(b)
  self.data=bytes(header)+b''.join(blocks);self.name=name
 def stat(self):return SimpleNamespace(st_size=len(self.data))
 def read_bytes(self):return self.data

def parse_eve_chart(raw,code,name='named-member.eve'):
 return import_ifs.parse_chart(_EveCarrier(raw,name),code)
