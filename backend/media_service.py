import hashlib
import json
from pathlib import Path
import cv2
import numpy as np
from PIL import Image
import io
from .observation_store import uid
from .store import now

MAX_IMAGE=20*1024*1024
MAX_VIDEO=100*1024*1024
MAX_PIXELS=16_000_000


class MediaService:
    def __init__(self,store): self.store=store

    def import_bytes(self,data,filename,metadata,parent_id=None,frame_seconds=None):
        suffix=Path(filename.replace('\\','/')).suffix.lower()
        allowed={'.png':'PNG','.jpg':'JPG','.jpeg':'JPG','.mp4':'MP4','.webm':'WEBM'}
        if suffix not in allowed: raise ValueError('只支持 PNG/JPG/MP4/WebM')
        kind=allowed[suffix]
        if not data or len(data)>(MAX_IMAGE if kind in ('PNG','JPG') else MAX_VIDEO): raise ValueError('文件为空或超过大小限制')
        if metadata.get('source_type') not in ('SIMULATOR','GAME_OBSERVATION'): raise ValueError('来源只能为 SIMULATOR 或 GAME_OBSERVATION')
        if metadata.get('authorized') is not True or len(metadata.get('authorization_note','').strip())<3:
            raise ValueError('必须提供用户拥有或获准使用的授权记录')
        digest=hashlib.sha256(data).hexdigest()
        media_id=uid()
        path=self.store.storage/(digest+suffix)
        preview=self.store.storage/(digest+'.preview.jpg')
        width=height=None;duration=None
        if kind in ('PNG','JPG'):
            if kind=='PNG' and not data.startswith(b'\x89PNG\r\n\x1a\n'): raise ValueError('PNG 文件签名不匹配')
            if kind=='JPG' and not data.startswith(b'\xff\xd8\xff'): raise ValueError('JPG 文件签名不匹配')
            with Image.open(io.BytesIO(data)) as im:
                width,height=im.size
                if width*height>MAX_PIXELS or width<2 or height<2: raise ValueError('图片尺寸不合法或过大')
                im.verify()
            image=cv2.imdecode(np.frombuffer(data,np.uint8),cv2.IMREAD_COLOR)
            if image is None: raise ValueError('图片解码失败')
            preview.write_bytes(cv2.imencode('.jpg',image,[cv2.IMWRITE_JPEG_QUALITY,90])[1].tobytes())
        else:
            if kind=='MP4' and (len(data)<12 or data[4:8]!=b'ftyp'): raise ValueError('MP4 文件签名不匹配')
            if kind=='WEBM' and not data.startswith(b'\x1a\x45\xdf\xa3'): raise ValueError('WebM 文件签名不匹配')
            path.write_bytes(data)
            cap=cv2.VideoCapture(str(path))
            try:
                fps=cap.get(cv2.CAP_PROP_FPS);frames=cap.get(cv2.CAP_PROP_FRAME_COUNT)
                width,height=int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)),int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
                if not cap.isOpened() or fps<=0 or frames<=0 or width*height>MAX_PIXELS: raise ValueError('视频解码或尺寸校验失败')
                duration=frames/fps
                if duration>600: raise ValueError('单段视频最长10分钟')
                ok,image=cap.read()
                if not ok: raise ValueError('视频无可解码帧')
                preview.write_bytes(cv2.imencode('.jpg',image,[cv2.IMWRITE_JPEG_QUALITY,85])[1].tobytes())
            finally: cap.release()
        if not path.exists(): path.write_bytes(data)
        def known(field):
            value=metadata.get(field)
            return None if value is None or str(value).strip() in ('','UNKNOWN','NULL') else str(value).strip()
        self.store.execute('''INSERT INTO observation_media VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
          (media_id,digest,kind,Path(filename.replace('\\','/')).name,str(path),str(preview),len(data),width,height,duration,parent_id,frame_seconds,
           known('captured_at'),now(),known('game_version'),known('server_info'),metadata.get('description',''),metadata['source_type'],
           metadata['authorization_note'].strip(),'UNREVIEWED'))
        self.store.event(media_id,'FRAME_EXTRACTED' if parent_id else 'OBSERVATION_IMPORTED','抽取视频帧' if parent_id else '导入授权画面资料',
                         {'media_id':media_id,'sha256':digest,'source_type':metadata['source_type'],'parent_id':parent_id,'frame_seconds':frame_seconds})
        return self.store.media(media_id)

    def extract(self,media_id,interval=2,max_frames=30):
        # 单库串行校验累计配额，防止并发请求越过抽帧上限。
        with self.store.lock:
            return self._extract(media_id,interval,max_frames)

    def _extract(self,media_id,interval=2,max_frames=30):
        if not 0.5<=interval<=600 or not 1<=max_frames<=60: raise ValueError('抽帧间隔0.5–600秒，数量1–60')
        media=self.store.media(media_id)
        if media['file_type'] not in ('MP4','WEBM'): raise ValueError('该资料不是视频')
        cap=cv2.VideoCapture(media['managed_path'])
        frames=[];previous=[];duplicates=0;limit_reached=False
        current=self.store.rows('SELECT id,byte_size,managed_path FROM observation_media WHERE parent_id=?',(media_id,))
        # 跨次抽帧也比较已存图像，避免有损视频解码差异绕过 SHA 去重。
        for stored in current:
            image=cv2.imread(stored['managed_path'])
            if image is not None:
                previous.append(cv2.resize(cv2.cvtColor(image,cv2.COLOR_BGR2GRAY),(32,32)).astype(np.float32))
        stored_count=len(current);stored_bytes=sum(r['byte_size'] for r in current)
        try:
            for index in range(min(max_frames,int(media['duration']/interval)+1)):
                offset=index*interval
                if offset>=media['duration']: break
                existing=self.store.rows('SELECT * FROM observation_media WHERE parent_id=? AND ABS(frame_seconds-?)<0.01',(media_id,offset))
                if existing: frames.append(existing[0]);continue
                if stored_count>=60: limit_reached=True;break
                cap.set(cv2.CAP_PROP_POS_MSEC,offset*1000)
                ok,image=cap.read()
                if not ok: break
                small=cv2.resize(cv2.cvtColor(image,cv2.COLOR_BGR2GRAY),(32,32)).astype(np.float32)
                if any(np.mean(np.abs(small-p))<1.0 for p in previous): duplicates+=1;continue
                previous.append(small)
                data=cv2.imencode('.png',image)[1].tobytes()
                if stored_bytes+len(data)>100*1024*1024: limit_reached=True;break
                if self.store.rows('SELECT id FROM observation_media WHERE parent_id=? AND sha256=?',(media_id,hashlib.sha256(data).hexdigest())):
                    duplicates+=1;continue
                metadata=dict(media,authorized=True)
                frames.append(self.import_bytes(data,f'frame-{offset:.2f}.png',metadata,media_id,offset))
                stored_count+=1;stored_bytes+=len(data)
        finally: cap.release()
        return {'frames':frames,'duplicates_skipped':duplicates,'max_frames':max_frames,'interval_seconds':interval,'limit_reached':limit_reached}
