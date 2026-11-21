"""Re-fetch closed Jetson evidence after a transfer failure; no motion requests."""
import argparse
import os
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from lab.backend import RemoteBackend
from lab.storage import Store
from lab.drive import UploadQueue
import json


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--run-id',required=True);p.add_argument('--root',default=str(Path(__file__).resolve().parents[1]/'experiments'))
    a=p.parse_args();s=Store(a.root);path=s.path(a.run_id)
    b=RemoteBackend(os.environ.get('D420_AGENT_URL','http://127.0.0.1:18866'),os.environ.get('D420_AGENT_TOKEN',''))
    b.download(a.run_id,path)
    manifest=json.loads((path/'manifest.json').read_text(encoding='utf-8'))
    s.finalize(a.run_id,manifest['status'],manifest['clock']);UploadQueue(s).retry(a.run_id)
    print('Recovered raw artifacts and rebuilt local results:',a.run_id)
