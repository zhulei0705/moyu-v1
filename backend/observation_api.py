import json
from pathlib import Path
from fastapi import APIRouter,File,Form,UploadFile,HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel,Field
from typing import Any,Literal
from datetime import datetime
from .media_service import MediaService,MAX_VIDEO
from .observation_vision import ObservationVision
from .observation_review import ReviewService


class MediaMetadata(BaseModel):
    authorized:Literal[True]
    source_type:Literal['GAME_OBSERVATION','SIMULATOR']
    authorization_note:str=Field(min_length=3,max_length=2000)
    captured_at:datetime|None=None
    game_version:str|None=Field(default=None,max_length=200)
    server_info:str|None=Field(default=None,max_length=200)
    description:str=Field(default='',max_length=5000)


class ExtractBody(BaseModel):
    interval_seconds:float=Field(default=2,ge=0.5,le=600)
    max_frames:int=Field(default=30,ge=1,le=60)


class AnalyzeBody(BaseModel):
    regions:list[dict[str,Any]]|None=None


class ReviewBody(BaseModel):
    action:Literal['confirm','correct','reject']
    reviewer:str=Field(min_length=2,max_length=100)
    rationale:str=Field(min_length=3,max_length=2000)
    field:str='visible_text'
    corrected_value:Any=None


class AnnotationBody(BaseModel):
    annotations:list[dict[str,Any]]
    author:str=Field(min_length=2,max_length=100)
    reason:str=Field(min_length=3,max_length=2000)
    base_revision:int=Field(default=0,ge=0)


def router(store,knowledge):
    routes=APIRouter(prefix='/api/observations',tags=['授权资料与人工观察'])
    media=MediaService(store);vision=ObservationVision(store,knowledge);review=ReviewService(store,knowledge)
    def call(func,*args):
        try:return func(*args)
        except (ValueError,OSError) as exc:raise HTTPException(422,str(exc))
    @routes.get('/status')
    def status():return dict(store.stats(),environment='OBSERVATION',passive=True,official_execution_enabled=False)
    @routes.get('/media')
    def list_media():return {'items':store.rows('SELECT * FROM observation_media ORDER BY imported_at DESC LIMIT 500')}
    @routes.post('/media')
    async def upload(file:UploadFile=File(...),metadata:str=Form(...)):
        try:meta=MediaMetadata.model_validate_json(metadata).model_dump(mode='json')
        except ValueError as exc:raise HTTPException(422,str(exc))
        data=bytearray()
        while chunk:=await file.read(1024*1024):
            data.extend(chunk)
            if len(data)>MAX_VIDEO:raise HTTPException(413,'文件超过100MB')
        return call(media.import_bytes,bytes(data),file.filename or '',meta)
    @routes.get('/media/{media_id}')
    def detail(media_id:str):
        m=call(store.media,media_id)
        return dict(m,observations=store.rows('SELECT * FROM observation WHERE media_id=? ORDER BY created_at',(media_id,)),
                    frames=store.rows('SELECT * FROM observation_media WHERE parent_id=? ORDER BY frame_seconds',(media_id,)),
                    annotations=store.rows('SELECT * FROM annotation_revision WHERE media_id=? ORDER BY revision DESC',(media_id,)))
    @routes.get('/media/{media_id}/preview')
    def preview(media_id:str):
        m=call(store.media,media_id)
        return FileResponse(m['preview_path'],media_type='image/jpeg',headers={'X-Content-Type-Options':'nosniff'})
    @routes.get('/media/{media_id}/file')
    def original(media_id:str):
        m=call(store.media,media_id)
        return FileResponse(m['managed_path'],media_type='application/octet-stream',filename=m['filename'],headers={'X-Content-Type-Options':'nosniff'})
    @routes.post('/media/{media_id}/extract')
    def extract(media_id:str,body:ExtractBody):return call(media.extract,media_id,body.interval_seconds,body.max_frames)
    @routes.post('/media/{media_id}/analyze')
    def analyze(media_id:str,body:AnalyzeBody):return call(vision.analyze,media_id,body.regions)
    @routes.get('/results')
    def results(status:str|None=None,kind:str|None=None):
        clauses=[];args=[]
        for k,v in [('status',status),('kind',kind)]:
            if v:clauses.append('o.'+k+'=?');args.append(v)
        return {'items':store.rows('''SELECT o.*,m.source_type,m.game_version FROM observation o JOIN observation_media m ON m.id=o.media_id'''+
                                   (' WHERE '+' AND '.join(clauses) if clauses else '')+' ORDER BY o.created_at DESC LIMIT 1000',args)}
    @routes.post('/results/{observation_id}/review')
    def review_result(observation_id:str,body:ReviewBody):return call(review.review,observation_id,body.model_dump())
    @routes.get('/results/{observation_id}')
    def evidence(observation_id:str):
        rows=store.rows('SELECT * FROM observation WHERE id=?',(observation_id,))
        if not rows:raise HTTPException(404,'观察不存在')
        return dict(rows[0],media=store.media(rows[0]['media_id']),reviews=store.rows('SELECT * FROM observation_review WHERE observation_id=? ORDER BY created_at',(observation_id,)))
    @routes.get('/facts')
    def facts():return {'items':store.rows('SELECT * FROM reviewed_observation_fact ORDER BY created_at DESC')}
    @routes.get('/conflicts')
    def conflicts():
        official={f['id']:f for f in knowledge.facts()}
        rows=store.rows('SELECT * FROM observation_conflict ORDER BY created_at DESC')
        return {'items':[dict(r,official_evidence=official.get(r['official_fact_id']),observed_evidence=store.rows('SELECT * FROM reviewed_observation_fact WHERE id=?',(r['reviewed_fact_id'],))[0]) for r in rows]}
    @routes.post('/media/{media_id}/annotations')
    def annotations(media_id:str,body:AnnotationBody):return call(review.annotate,media_id,body.model_dump())
    return routes
