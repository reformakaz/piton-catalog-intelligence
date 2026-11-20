#!/usr/bin/env python3
"""Safe installer. Does not edit existing PitON services."""
import pathlib, shutil, subprocess, sys, os
SRC=pathlib.Path(__file__).resolve().parent
DST=pathlib.Path('/opt/piton-catalog-intelligence')
if os.geteuid()!=0:sys.exit('Нужен sudo')
if DST.exists():sys.exit('Каталог уже существует; установка остановлена. Для обновления нужна отдельная процедура.')
shutil.copytree(SRC,DST,ignore=shutil.ignore_patterns('catalogs.sqlite3','reports','__pycache__'))
subprocess.check_call([sys.executable,str(DST/'catalog_engine.py'),'init'])
print('INSTALL OK:',DST)
