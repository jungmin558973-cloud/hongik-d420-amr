"""OAuth credentials in OS keyring, durable retries and idempotent verified uploads."""
import hashlib
import json
import os
from pathlib import Path
import time
from .storage import sha

SERVICE='HONGIK_CAFE_D420'
SCOPES=['https://www.googleapis.com/auth/drive.file']


def authorize(client_file):
    import keyring
    from google_auth_oauthlib.flow import InstalledAppFlow
    credentials=InstalledAppFlow.from_client_secrets_file(client_file,SCOPES).run_local_server(port=0)
    keyring.set_password(SERVICE,'drive_oauth',credentials.to_json())


def client():
    import keyring
    from google.oauth2.credentials import Credentials
    from google.auth.transport.requests import Request
    from googleapiclient.discovery import build
    secret=keyring.get_password(SERVICE,'drive_oauth')
    if not secret:raise ValueError('Drive OAuth not configured; data retained locally')
    credentials=Credentials.from_authorized_user_info(json.loads(secret),SCOPES)
    if credentials.expired and credentials.refresh_token:
        credentials.refresh(Request());keyring.set_password(SERVICE,'drive_oauth',credentials.to_json())
    return build('drive','v3',credentials=credentials,cache_discovery=False)


class DriveTransport:
    def __init__(self,parent):
        if not parent or any(x in parent for x in "'/\\"):raise ValueError('Set D420_DRIVE_FOLDER_ID to a raw folder ID')
        self.parent=parent;self.api=client()

    def find(self,parent,key):
        q=f"'{parent}' in parents and trashed=false and appProperties has {{ key='d420_key' and value='{key}' }}"
        return self.api.files().list(q=q,fields='files(id,md5Checksum,size)',pageSize=100).execute().get('files',[])

    def folder(self,rid,display_name=None):
        matches=self.find(self.parent,rid)
        if matches:return matches[0]['id']
        return self.api.files().create(body={'name':display_name or rid,'mimeType':'application/vnd.google-apps.folder',
               'parents':[self.parent],'appProperties':{'d420_key':rid}},fields='id').execute()['id']

    def file(self,parent,path,key,progress):
        from googleapiclient.http import MediaFileUpload
        with Path(path).open('rb') as f:md5=hashlib.file_digest(f,'md5').hexdigest()
        old=self.find(parent,key)
        if old and old[0].get('md5Checksum')==md5 and int(old[0].get('size',-1))==Path(path).stat().st_size:return old[0]['id']
        media=MediaFileUpload(str(path),resumable=True,chunksize=1024*1024)
        if old:
            request=self.api.files().update(fileId=old[0]['id'],media_body=media,fields='id,md5Checksum,size')
        else:
            request=self.api.files().create(body={'name':Path(path).name,'parents':[parent],
                   'appProperties':{'d420_key':key}},media_body=media,fields='id,md5Checksum,size')
        response=None
        while response is None:
            status,response=request.next_chunk(num_retries=2)
            if status:progress(status.progress())
        check=self.api.files().get(fileId=response['id'],fields='id,md5Checksum,size').execute()
        if check.get('md5Checksum')!=md5 or int(check.get('size',-1))!=Path(path).stat().st_size:raise ValueError('Remote checksum/size verification failed')
        return check['id']


class UploadQueue:
    def __init__(self,store,factory=None):
        self.store=store;self.factory=factory or (lambda:DriveTransport(os.environ.get('D420_DRIVE_FOLDER_ID','')))
        # A process may have died during an upload. Idempotent lookup resumes it.
        with store.connect() as db:db.execute("UPDATE upload_queue SET state='PENDING' WHERE state='UPLOADING'")

    def status(self):
        with self.store.connect() as db:return [dict(x) for x in db.execute('SELECT * FROM upload_queue')]

    def retry(self,rid=None):
        with self.store.connect() as db:
            if rid:db.execute("UPDATE upload_queue SET next_at=0,state='PENDING' WHERE run_id=?",(rid,))
            else:db.execute("UPDATE upload_queue SET next_at=0,state='PENDING' WHERE state!='COMPLETE'")

    def once(self,now=None):
        now=time.time() if now is None else now
        with self.store.connect() as db:
            r=db.execute("SELECT * FROM upload_queue WHERE state IN ('PENDING','RETRY') AND next_at<=? ORDER BY next_at LIMIT 1",(now,)).fetchone()
            if not r:return False
            rid=r['run_id'];db.execute("UPDATE upload_queue SET state='UPLOADING' WHERE run_id=?",(rid,))
        try:
            path=self.store.path(rid);manifest=json.loads((path/'manifest.json').read_text(encoding='utf-8'))
            # Verify immutable local evidence before any network write.
            for rel,meta in manifest['files'].items():
                if sha(path/rel)!=meta['sha256']:raise ValueError('Local SHA-256 mismatch: '+rel)
            transport=self.factory();folder=transport.folder(rid,manifest.get('display_name')) if isinstance(transport,DriveTransport) else transport.folder(rid);folders={'':folder}
            files=[(path/rel,Path(rel)) for rel in manifest['files']]+[(path/'manifest.json',Path('manifest.json'))]
            bundle=self.store.bundle_path(rid)
            if bundle.is_file():files.append((bundle,Path(bundle.name)))
            for n,(p,rel) in enumerate(files):
                parent=''
                for part in rel.parts[:-1]:
                    sub=(Path(parent)/part).as_posix()
                    if sub not in folders:
                        # Stable folder keys within run; idempotent after restart.
                        matches=transport.find(folders[parent],hashlib.sha256(sub.encode()).hexdigest())
                        if matches:folders[sub]=matches[0]['id']
                        else:
                            folders[sub]=transport.api.files().create(body={'name':part,'mimeType':'application/vnd.google-apps.folder','parents':[folders[parent]],'appProperties':{'d420_key':hashlib.sha256(sub.encode()).hexdigest()}},fields='id').execute()['id']
                    parent=sub
                def progress(frac):
                    with self.store.connect() as db:db.execute('UPDATE upload_queue SET progress=? WHERE run_id=?',((n+frac)/len(files),rid))
                transport.file(folders[parent],p,hashlib.sha256(rel.as_posix().encode()).hexdigest(),progress)
            with self.store.connect() as db:
                db.execute("UPDATE upload_queue SET state='COMPLETE',progress=1,error=NULL,remote_id=? WHERE run_id=?",(folder,rid))
                db.execute("UPDATE experiment_index SET upload_status='COMPLETE' WHERE run_id=?",(rid,))
        except Exception as exc:
            # Never log credentials, URLs or provider exception text containing requests.
            error=type(exc).__name__+': upload unavailable; check OAuth/folder/network or local manifest'
            attempts=r['attempts']+1
            with self.store.connect() as db:
                db.execute("UPDATE upload_queue SET state='RETRY',attempts=?,next_at=?,error=? WHERE run_id=?",(attempts,now+min(3600,2**min(attempts,10)*5),error,rid))
                db.execute("UPDATE experiment_index SET upload_status='RETRY' WHERE run_id=?",(rid,))
        return True


if __name__=='__main__':
    import argparse
    p=argparse.ArgumentParser();p.add_argument('--authorize',required=True);args=p.parse_args();authorize(args.authorize)
