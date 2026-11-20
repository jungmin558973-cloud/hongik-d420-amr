"""Browser-compatible H.264 overlay video with original frame receive timing."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import statistics
import cv2
from .storage import read_jsonl,dump


def make_browser_video(path,progress=None):
    progress=progress or (lambda **kwargs:None)
    source=path/'webcam_annotated.mp4';metadata=read_jsonl(path/'camera_analysis.jsonl')
    if not source.exists() or not metadata:raise ValueError('액면 정밀 분석을 먼저 실행하세요.')
    times=[r['utc']-metadata[0]['utc'] for r in metadata]
    if any(b<=a for a,b in zip(times,times[1:])):raise ValueError('프레임 시각 순서 오류: 재생 영상을 생성하지 않았습니다.')
    if shutil.disk_usage(path).free<512*1024**2:raise ValueError('재생 영상 생성에 512MiB 이상 여유 공간이 필요합니다.')
    import imageio_ffmpeg
    ffmpeg=imageio_ffmpeg.get_ffmpeg_exe()
    target=path/'webcam_analysis_browser.mp4';temp=path/'webcam_analysis_browser.tmp.mp4'
    cap=cv2.VideoCapture(str(source));count=0
    try:
        if not cap.isOpened():raise ValueError('검출 표시 영상을 열 수 없습니다.')
        with tempfile.TemporaryDirectory(prefix='_playback_',dir=path) as folder:
            folder=Path(folder);lines=['ffconcat version 1.0']
            median=statistics.median([b-a for a,b in zip(times,times[1:])]) if len(times)>1 else 1/30
            for i,t in enumerate(times):
                ok,frame=cap.read()
                if not ok:raise ValueError('검출 영상과 프레임 시각 수가 일치하지 않습니다.')
                name=f'{i:07d}.jpg'
                if not cv2.imwrite(str(folder/name),frame,[cv2.IMWRITE_JPEG_QUALITY,94]):raise ValueError('재생용 임시 프레임 저장 실패')
                duration=times[i+1]-t if i+1<len(times) else median
                lines.extend([f"file '{name}'",'option framerate 1000',f'duration {duration:.9f}']);count+=1
                if count%30==0:progress(stage='검출 영상 재생 준비',percent=90+5*count/len(times),done=count,total=len(times))
            extra,_=cap.read()
            if extra:raise ValueError('시각 정보가 없는 추가 영상 프레임이 있습니다.')
            (folder/'frames.txt').write_text('\n'.join(lines)+'\n',encoding='utf-8')
            progress(stage='브라우저 영상 인코딩',percent=96)
            result=subprocess.run([ffmpeg,'-hide_banner','-loglevel','error','-y','-f','concat','-safe','0','-i','frames.txt',
                '-an','-c:v','libx264','-preset','veryfast','-crf','19','-pix_fmt','yuv420p','-fps_mode','vfr',
                '-video_track_timescale','1000000','-movflags','+faststart',str(temp.resolve())],cwd=folder,capture_output=True,timeout=300,
                creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
            if result.returncode:raise ValueError('재생 영상 인코딩 실패: '+result.stderr.decode(errors='replace')[-600:])
        check=cv2.VideoCapture(str(temp))
        try:encoded_count=round(check.get(cv2.CAP_PROP_FRAME_COUNT))
        finally:check.release()
        if encoded_count!=len(times):raise ValueError(f'재생 영상 프레임 수 불일치: {encoded_count}/{len(times)}')
        os.replace(temp,target)
        info={'file':target.name,'codec':'H.264','timing':'variable frame rate from original UTC; 1ms input timebase',
              'frames':count,'timeline_origin_utc':metadata[0]['utc'],'last_frame_time_s':times[-1],
              'max_input_quantization_ms':1,'original_video_preserved':True}
        dump(path/'browser_video.json',info);return info
    finally:
        cap.release();temp.unlink(missing_ok=True)
