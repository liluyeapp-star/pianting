import csv
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
import numpy as np
import soundfile as sf
ROOT=Path(__file__).parents[1]
class RenderTests(unittest.TestCase):
 def test_end_to_end_selection_duration_and_mapping(self):
  with tempfile.TemporaryDirectory() as d:
   d=Path(d)
   subprocess.run([sys.executable,str(ROOT/'examples/demo.py'),'--output-dir',str(d)],check=True,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
   raw=d/'output.wav'
   subprocess.run(['ffmpeg','-v','error','-i',str(d/'out/guest.mp3'),str(raw)],check=True)
   audio,rate=sf.read(raw)
   self.assertAlmostEqual(len(audio)/rate,4.2,delta=.035)
   for start,end in ((.1,1.9),(2.3,4.1)):
    clip=audio[int(rate*start):int(rate*end)]; freq=np.fft.rfftfreq(len(clip),1/rate)[np.argmax(abs(np.fft.rfft(clip)))]
    self.assertAlmostEqual(freq,660,delta=2)
   with (d/'out/时间映射.csv').open(encoding='utf-8-sig') as f: rows=list(csv.reader(f))
   self.assertEqual(rows[1][2:4],['2.000000','4.000000'])
   self.assertEqual(rows[3][2:4],['6.000000','8.000000'])
 def test_overlapping_segments_rejected(self):
  with tempfile.TemporaryDirectory() as d:
   d=Path(d);sf.write(d/'source.wav',np.zeros(48000),48000)
   (d/'keep.json').write_text(json.dumps([{'start':0,'end':.7},{'start':.6,'end':1}]))
   result=subprocess.run([sys.executable,str(ROOT/'skills/pianting/scripts/render_guest.py'),'--source',str(d/'source.wav'),'--segments',str(d/'keep.json'),'--output-dir',str(d/'out'),'--work-dir',str(d/'work')],capture_output=True)
   self.assertNotEqual(result.returncode,0);self.assertFalse((d/'out/guest.mp3').exists())
if __name__=='__main__':unittest.main()
