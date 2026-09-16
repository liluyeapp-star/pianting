#!/usr/bin/env python3
"""Check local dependencies only; does not log into any service."""
import importlib.util
import shutil
import sys
checks={'Python >= 3.9':sys.version_info>=(3,9),'numpy':importlib.util.find_spec('numpy') is not None,'soundfile':importlib.util.find_spec('soundfile') is not None,'ffmpeg':bool(shutil.which('ffmpeg')),'ffprobe':bool(shutil.which('ffprobe'))}
for name,ok in checks.items():print(('OK  ' if ok else '缺少 ')+name)
print(('可用' if shutil.which('lark-cli') else '未安装')+' lark-cli（自动飞书转写需要；手动导出 TXT 则不需要）')
print(('可用' if shutil.which('node') else '未安装')+' Node.js（仅可选托管和 Worker 测试需要）')
print('此检查不代表飞书已授权或模型识别准确。')
sys.exit(0 if all(checks.values()) else 1)
