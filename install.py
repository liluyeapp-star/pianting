#!/usr/bin/env python3
"""Install the self-contained skill without replacing an existing copy."""
import argparse
import shutil
from pathlib import Path
p=argparse.ArgumentParser(description='安装偏听则明 Skill；不覆盖既有目录')
p.add_argument('--dest',required=True,type=Path,help='助手的 skills 根目录')
a=p.parse_args(); source=Path(__file__).resolve().parent/'skills/pianting'; target=a.dest.expanduser()/'pianting'
if target.exists():p.error('目标已存在，请先自行备份或选择其他目录：'+str(target))
target.parent.mkdir(parents=True,exist_ok=True)
shutil.copytree(source,target,ignore=shutil.ignore_patterns('__pycache__','*.pyc'))
print('已安装到 '+str(target.resolve()))
print('安装只复制技能；请确保运行助手时使用已安装依赖的 Python 环境。')
