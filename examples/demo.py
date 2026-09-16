#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Offline mechanical demo. Tones represent speakers, not synthesized voices."""
import argparse
import json
import subprocess
import sys
import wave
from pathlib import Path
import numpy as np
p=argparse.ArgumentParser(description='离线音调演示：验证剪辑机制，不测试说话人识别')
p.add_argument('--output-dir',required=True,type=Path)
a=p.parse_args(); root=a.output_dir.resolve();root.mkdir(parents=True,exist_ok=True)
if (root/'source.wav').exists():p.error('演示目录已有音频，请选择新的目录')
rate=48000
samples=[]
for hz in (330,660,330,660):
 t=np.arange(rate*2)/rate
 samples.append((np.sin(2*np.pi*hz*t)*0.12*32767).astype('<i2'))
with wave.open(str(root/'source.wav'),'wb') as f:
 f.setnchannels(1);f.setsampwidth(2);f.setframerate(rate);f.writeframes(np.concatenate(samples).tobytes())
(root/'transcript.txt').write_text('主持 00:00:00.000\n[330Hz音调]\n嘉宾 00:00:02.000\n[660Hz音调]\n主持 00:00:04.000\n[330Hz音调]\n嘉宾 00:00:06.000\n[660Hz音调]\n',encoding='utf-8')
scripts=Path(__file__).resolve().parents[1]/'skills/pianting/scripts'
subprocess.run([sys.executable,str(scripts/'segments_from_feishu.py'),'--transcript',str(root/'transcript.txt'),'--speaker','嘉宾','--duration','8','--output',str(root/'segments.json')],check=True)
subprocess.run([sys.executable,str(scripts/'render_guest.py'),'--source',str(root/'source.wav'),'--segments',str(root/'segments.json'),'--output-dir',str(root/'out'),'--work-dir',str(root/'scratch'),'--title','偏听则明：离线音调演示'],check=True)
subprocess.run(['ffmpeg','-v','error','-i',str(root/'out/guest.mp3'),'-f','null','-'],check=True)
print('完成：8秒的交替音调 -> 约4.2秒的嘉宾音调；结果没有经过语音识别。')
