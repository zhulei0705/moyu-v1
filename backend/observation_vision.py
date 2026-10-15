import importlib.metadata
import re
import threading
import json
import math
import cv2
import numpy as np
from .world import COLORS
from .observation_store import uid
from .store import now

KINDS=('OCR_TEXT','GAME_COORDINATE','MAP_NAME','PLAYER','MONSTER_NAME','BOSS_NAME','QUEST_TEXT','INVENTORY_TEXT')


def validate_region(region,width,height):
    if not isinstance(region,list) or len(region)!=4 or any(type(v) not in (int,float) or not math.isfinite(v) for v in region): raise ValueError('区域必须为有限数值 [x,y,width,height]')
    x,y,w,h=map(int,region)
    if x<0 or y<0 or w<=0 or h<=0 or x+w>width or y+h>height: raise ValueError('区域超出原始画面')
    return [x,y,w,h]


class ObservationVision:
    def __init__(self,store,knowledge):
        self.store,self.knowledge=store,knowledge
        self.engine=None
        self.lock=threading.Lock()
        self.algorithm='RapidOCR-'+importlib.metadata.version('rapidocr')+'-PP-OCRv6-small/onnx-cpu'

    def analyze(self,media_id,regions=None):
        media=self.store.media(media_id)
        if media['file_type'] not in ('PNG','JPG'): raise ValueError('请先对视频抽帧，再分析图像')
        image=cv2.imread(media['managed_path'])
        if image is None: raise ValueError('原始图像不可读')
        height,width=image.shape[:2]
        regions=regions or [{'kind':'OCR_TEXT','region':[0,0,width,height]}]
        if len(regions)>12: raise ValueError('一次最多12个 OCR 区域')
        for r in regions:
            if r.get('kind') not in KINDS: raise ValueError('未知识别类型')
            r['region']=validate_region(r['region'],width,height)
        analysis_id=uid();results=[]
        bosses={row['name'] for row in self.knowledge.entities('bosses')}
        maps={f['normalized_value'] for f in self.knowledge.facts('dungeon') if f['field'] in ('entry_map','entry_location') and isinstance(f['normalized_value'],str)}
        def save(kind,raw,value,region,confidence,algorithm=None,error=None):
            result={'id':uid(),'media_id':media_id,'analysis_id':analysis_id,'kind':kind,'raw_text':raw,'value':value,'region':region,
                    'coordinate_system':'SCREEN_PIXEL','algorithm_version':algorithm or self.algorithm,'confidence':confidence,
                    'status':'UNKNOWN' if value is None else 'PENDING','created_at':now(),'error':error}
            self.store.execute('INSERT INTO observation VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)',
              (result['id'],media_id,analysis_id,kind,raw,json.dumps(value,ensure_ascii=False),json.dumps(region),'SCREEN_PIXEL',result['algorithm_version'],
               confidence,result['status'],result['created_at'],error))
            results.append(result)
            if value is None: self.store.event(analysis_id,'UNKNOWN_RECORDED','识别未得到可确认值',{'observation_id':result['id'],'kind':kind,'error':error})
            return result
        with self.lock:
            try:
                if self.engine is None:
                    from rapidocr import RapidOCR
                    self.engine=RapidOCR(params={'EngineConfig.onnxruntime.intra_op_num_threads':2,'EngineConfig.onnxruntime.inter_op_num_threads':2})
                for region in regions:
                    x,y,w,h=region['region'];kind=region['kind']
                    output=self.engine(image[y:y+h,x:x+w],text_score=0.1)
                    if not output.txts:
                        save(kind,None,None,region['region'],None)
                        continue
                    for box,raw,score in zip(output.boxes,output.txts,output.scores):
                        left=max(0,int(math.floor(np.min(box[:,0])))+x);top=max(0,int(math.floor(np.min(box[:,1])))+y)
                        right=min(width,int(math.ceil(np.max(box[:,0])))+x);bottom=min(height,int(math.ceil(np.max(box[:,1])))+y)
                        bbox=[left,top,max(1,right-left),max(1,bottom-top)]
                        recognized=raw.strip()
                        value=recognized
                        if kind=='GAME_COORDINATE':
                            match=re.search(r'(\d{1,5})\s*[,，]\s*(\d{1,5})',recognized)
                            value=[int(match[1]),int(match[2])] if match else None
                        if kind=='PLAYER': value=None # 不用文字框推断角色图像位置。
                        save(kind,raw,value,bbox,float(score))
                        if kind=='OCR_TEXT':
                            match=re.search(r'坐标\s*[:：]?\s*(\d{1,5})\s*[,，]\s*(\d{1,5})',recognized)
                            if match: save('GAME_COORDINATE',raw,[int(match[1]),int(match[2])],bbox,float(score))
                            for boss in bosses:
                                if boss in recognized: save('BOSS_NAME',raw,boss,bbox,float(score))
                            for name in maps:
                                if name in recognized: save('MAP_NAME',raw,name,bbox,float(score))
                            if re.search(r'任务|提示|击败|前往',recognized): save('QUEST_TEXT',raw,recognized,bbox,float(score))
                            if re.search(r'背包|碎片|晶石|幻兽|物品',recognized): save('INVENTORY_TEXT',raw,recognized,bbox,float(score))
            except Exception as exc:
                save('OCR_TEXT',None,None,[0,0,width,height],None,error=str(exc))
        # 原创模拟器的色块检测只读取像素，并明确限定 SIMULATOR；官服没有角色模板就保持 UNKNOWN。
        if media['source_type']=='SIMULATOR':
            for label,color in [('PLAYER',COLORS['player']),('MONSTER_OBJECT',COLORS['monster'])]:
                low=np.clip(np.array(color,dtype=int)-3,0,255).astype(np.uint8);high=np.clip(np.array(color,dtype=int)+3,0,255).astype(np.uint8)
                mask=cv2.inRange(image,low,high)
                count,_,stats,_=cv2.connectedComponentsWithStats(mask)
                for i in range(1,count):
                    if stats[i,cv2.CC_STAT_AREA]<20:continue
                    region=stats[i,:4].astype(int).tolist()
                    save(label,None,{'screen_region':region,'name':None},region,1.0,'simulator-pixel-color-v1.2')
                    self.store.event(analysis_id,'OBJECT_DETECTED','从模拟图像像素检测可见色块',{'kind':label,'region':region,'media_id':media_id,'source_type':'SIMULATOR'})
        detected={r['kind'] for r in results}
        for kind in KINDS:
            if kind not in detected: save(kind,None,None,[0,0,width,height],None)
        self.store.event(analysis_id,'OCR_COMPLETED','完成本地 OCR 与图像分析',{'media_id':media_id,'analysis_id':analysis_id,'results':len(results),'algorithm_version':self.algorithm})
        return {'analysis_id':analysis_id,'observations':results,'automatic_confirmation':False}
