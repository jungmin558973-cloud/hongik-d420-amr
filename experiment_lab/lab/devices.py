"""Windows DirectShow device names; never assume device zero is the C920."""
import json
import os
from pathlib import Path
import subprocess
import sys


def enumerate_devices():
    if os.name!='nt':return []
    from pygrabber.dshow_graph import FilterGraph
    return [{'index':i,'name':name,'backend':'DSHOW'} for i,name in enumerate(FilterGraph().get_input_devices())]


def devices():
    if os.name!='nt':return []
    p=subprocess.run([sys.executable,'-m','lab.devices'],capture_output=True,text=True,encoding='utf-8',timeout=8,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    if p.returncode:raise ValueError('Camera name enumeration failed; check pygrabber/comtypes installation')
    return json.loads(p.stdout)


def resolve(source='auto',backend='DSHOW',inventory=None):
    found=devices() if inventory is None else inventory
    if source=='auto':
        choices=[d for d in found if 'C920' in d['name'].upper()]
        if len(choices)!=1:raise ValueError('C920 not uniquely detected. Connect USB camera and select its name; laptop camera is not selected automatically.')
        d=choices[0];return {'source':d['index'],'device_name':d['name'],'backend':'DSHOW','identity_verified':True}
    if isinstance(source,str) and source.isdigit():source=int(source)
    if type(source) is int:
        match=next((d for d in found if d['index']==source),None)
        name=match['name'] if match and backend=='DSHOW' else f'{backend} device {source} (name unverified)'
        return {'source':source,'device_name':name,'backend':backend,'identity_verified':bool(match and backend=='DSHOW')}
    return {'source':source,'device_name':str(source),'backend':backend,'identity_verified':False}


if __name__=='__main__':
    print(json.dumps(enumerate_devices(),ensure_ascii=True))
