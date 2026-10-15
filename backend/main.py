import asyncio
import json
import os
from pathlib import Path
from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.trustedhost import TrustedHostMiddleware
from pydantic import BaseModel, Field
from .store import Store
from .runtime import Runtime
from .knowledge import KnowledgeDB, router as knowledge_router
from .observation_store import ObservationStore
from .observation_api import router as observation_router
from .twin import TwinService, router as twin_router
from .research_scene import SCENE_ID,FAULTS,prepare_profile
from .reliability import ServiceHealth

ROOT = Path(__file__).resolve().parents[1]


class Start(BaseModel):
    rounds: int = Field(1, ge=1, le=100)
    seed: int = Field(1, ge=0, le=2147483647)
    fault: str | None = None
    scene_id: str = 'original'


def create_app(db_path=None, tick=0.08):
    from .db_isolation import database_path, record_start
    path = database_path(db_path)
    record_start(path)
    path.parent.mkdir(parents=True,exist_ok=True)
    store = Store(path)
    runtime = Runtime(store,tick)
    service_health=ServiceHealth(store,runtime)
    runtime.on_failure=lambda exc:service_health.event('WORKER_EXCEPTION',{'error':str(exc),'run_id':runtime.run_id})
    @asynccontextmanager
    async def lifespan(app):
        service_health.start()
        heartbeat=asyncio.create_task(service_health.pulse())
        try:yield
        finally:
            try:
                if runtime.thread and runtime.thread.is_alive():runtime.control('stop')
            finally:
                heartbeat.cancel()
                try:await heartbeat
                except asyncio.CancelledError:pass
                service_health.stopped()
                if os.getenv('MOYU_MANAGED')=='1' and not (runtime.thread and runtime.thread.is_alive()):
                    for repository in (app.state.observation,app.state.knowledge,store):
                        with repository.lock:repository.db.close()
    app = FastAPI(title='魔域 Web Agent V1.3.3 · 长期运行容量',lifespan=lifespan)
    app.add_middleware(TrustedHostMiddleware,allowed_hosts=['127.0.0.1','localhost','[::1]','testserver'])
    app.state.runtime, app.state.store = runtime, store
    knowledge_repository=KnowledgeDB(path)
    app.state.knowledge=knowledge_repository
    app.include_router(knowledge_router(knowledge_repository))
    observation_repository=ObservationStore(path,path.parent/'observation-media',store)
    app.state.observation=observation_repository
    app.include_router(observation_router(observation_repository,knowledge_repository))
    twin_service=TwinService(observation_repository,knowledge_repository)
    app.include_router(twin_router(twin_service))
    runtime.profile_factory=lambda:prepare_profile(twin_service)

    @app.middleware('http')
    async def local_only(request, call_next):
        host = request.client.host if request.client else ''
        if host not in ('127.0.0.1','::1','localhost','testclient'):
            return Response('仅允许本机访问',403)
        if request.method == 'POST':
            # 同源与专用请求头同时校验，防止本地控制 API 被跨站调用。
            origin = request.headers.get('origin')
            allowed_origins = {str(request.base_url).rstrip('/'),'http://127.0.0.1:5173','http://localhost:5173'}
            if request.headers.get('x-agent-control') != 'local-console' or (origin and origin not in allowed_origins):
                return Response('控制请求校验失败',403)
        return await call_next(request)

    @app.get('/api/health')
    def health(): return {'status':'ok','source':'SIMULATED','environment':'SIMULATOR','version':'1.3.3','instance_id':service_health.id,'pid':os.getpid(),'database_path':str(path),'execution_environment':os.getenv('MOYU_ENV','PRODUCTION'),'source_sha256':os.getenv('MOYU_SOURCE_SHA256')}
    @app.get('/api/storage/status')
    def storage_status():return store.evidence.status()
    @app.get('/api/service/status')
    def service_status():return service_health.status()
    @app.get('/api/agent/state-history')
    def state_history(run_id:str|None=None,limit:int=100):return {'items':store.query('SELECT * FROM agent_state_history'+(' WHERE run_id=?' if run_id else '')+' ORDER BY id DESC LIMIT ?',(run_id,max(1,min(limit,1000))) if run_id else (max(1,min(limit,1000)),))}
    @app.get('/api/agent/status')
    def status(): return dict(runtime.status(),environment='SIMULATOR',knowledge_mode='READ_ONLY',official_execution_enabled=False)
    @app.post('/api/agent/start')
    def start(body: Start):
        if body.fault not in ((None,'stuck','crash')+FAULTS if body.scene_id==SCENE_ID else (None,'stuck','crash')): raise HTTPException(422,'不支持的故障类型')
        try: runtime.start(body.rounds,body.seed,body.fault,body.scene_id)
        except ValueError as e: raise HTTPException(409,str(e))
        return runtime.status()
    @app.get('/api/scenes')
    def scenes():return {'items':[{'id':'original','name':'原创四阶段模拟副本','source':'SIMULATOR'}, {'id':SCENE_ID,'name':'九星研究副本','source':'SIMULATOR','official_execution_enabled':False}]}
    @app.get('/api/research/decisions')
    def decisions(run_id:str|None=None,limit:int=200):return {'items':runtime.research.decisions(run_id,max(1,min(limit,1000)))}
    @app.get('/api/research/runs/{run_id}')
    def research_run(run_id:str):
        rows=store.query('SELECT * FROM research_run WHERE run_id=?',(run_id,))
        if not rows:raise HTTPException(404,'研究运行不存在')
        return dict(rows[0],configuration=json.loads(rows[0]['configuration']))
    @app.get('/api/research/observations/{observation_id}/frame')
    def research_frame(observation_id:str):
        png=store.evidence.resolve(observation_id)
        if png is None:raise HTTPException(404,'执行观察不存在')
        return Response(bytes(png),media_type='image/png')
    @app.get('/api/research/earnings')
    def research_earnings():return runtime.research.earnings()
    @app.get('/api/research/settlements')
    def settlements(run_id:str|None=None):
        return {'source':'SIMULATOR','items':store.query('SELECT * FROM research_settlement'+(' WHERE run_id=?' if run_id else '')+' ORDER BY settlement_time DESC LIMIT 1000',(run_id,) if run_id else ())}
    @app.get('/api/research/future-resource-records/schema')
    def future_schema():return {'status':'RESERVED_NOT_IMPLEMENTED','required_source':'GAME_OBSERVATION','required_review':'MANUAL_REVIEW','record_types':['RESOURCE_OBSERVATION','IN_GAME_TRADE'],'required_fields':['reviewed_fact_ids','game_version','observed_at','item','quantity','in_game_currency','evidence_ids'],'real_money_conversion':False}
    @app.post('/api/research/future-resource-records')
    def future_write():raise HTTPException(501,'未来人工审核资源与游戏内成交接口预留，未接入，不生成真实收益')
    for command in ('pause','resume','stop','emergency-stop'):
        def make_endpoint(action):
            def endpoint():
                try: runtime.control(action)
                except ValueError as e: raise HTTPException(409,str(e))
                return runtime.status()
            return endpoint
        app.add_api_route('/api/agent/'+command,make_endpoint(command),methods=['POST'])
    @app.get('/api/runs')
    def runs(): return store.query("SELECT r.*,COALESCE(x.scene_id,'original') AS scene_id FROM runs r LEFT JOIN research_run x ON x.run_id=r.id ORDER BY r.started DESC LIMIT 200")
    @app.get('/api/runs/{run_id}')
    def run(run_id: str):
        rows = store.query('SELECT * FROM runs WHERE id=?',(run_id,))
        if not rows: raise HTTPException(404,'任务不存在')
        return dict(rows[0],events=store.query('SELECT * FROM logs WHERE runId=? ORDER BY id',(run_id,)))
    @app.get('/api/logs/export')
    def export(runId: str | None=None):
        # A stable upper cursor prevents export chasing a continuously running worker.
        upper=store.query('SELECT COALESCE(MAX(id),0) AS n FROM logs')[0]['n']
        def chunks():
            cursor=0
            while cursor<upper:
                rows=store.query('SELECT * FROM logs WHERE id>? AND id<=?'+(' AND runId=?' if runId else '')+' ORDER BY id LIMIT 100',
                                 (cursor,upper,runId) if runId else (cursor,upper))
                if not rows:break
                for row in rows:
                    cursor=row['id']
                    yield json.dumps(row,ensure_ascii=False)+'\n'
        return StreamingResponse(chunks(),media_type='application/x-ndjson',headers={'Content-Disposition':'attachment; filename=events.jsonl'})
    @app.get('/api/logs')
    def logs(after: int=0, runId: str | None=None, level: str | None=None, search: str='', limit: int=500, tail: bool=False):
        clauses, args = ['id>?'], [after]
        for key,value in [('runId',runId),('level',level)]:
            if value: clauses.append(key+'=?'); args.append(value)
        if search: clauses.append('message LIKE ?'); args.append('%'+search+'%')
        args.append(max(1,min(limit,2000)))
        rows=store.query('SELECT * FROM logs WHERE '+' AND '.join(clauses)+(' ORDER BY id DESC' if tail else ' ORDER BY id')+' LIMIT ?',args)
        return rows[::-1] if tail else rows
    @app.get('/api/logs/stream')
    async def stream(request: Request, after: int=0):
        if service_health.sse_clients>=128:raise HTTPException(503,'SSE连接达到上限')
        try: cursor = max(after,int(request.headers.get('last-event-id','0')))
        except ValueError: raise HTTPException(400,'无效 Last-Event-ID')
        service_health.sse_clients+=1
        async def generate():
            nonlocal cursor
            try:
                while not await request.is_disconnected():
                    rows = await asyncio.to_thread(store.query,'SELECT * FROM logs WHERE id>? ORDER BY id LIMIT 100',(cursor,))
                    for row in rows:
                        cursor = row['id']
                        yield f'id: {cursor}\ndata: {json.dumps(row,ensure_ascii=False)}\n\n'
                    if not rows: yield ': heartbeat\n\n'
                    # Yield fairly while catching up; idle polling alone needs
                    # a delay. A per-page delay made large history take minutes.
                    await asyncio.sleep(0 if rows else 0.2)
            finally:service_health.sse_clients-=1
        return StreamingResponse(generate(),media_type='text/event-stream',headers={'Cache-Control':'no-cache','X-Accel-Buffering':'no'})
    @app.get('/api/frame/latest')
    def frame(): return Response(runtime.frame(),media_type='image/png',headers={'Cache-Control':'no-store'})
    @app.get('/api/inventory')
    def inventory():
        with runtime.lock: return dict(source='SIMULATED',loaded=runtime.world is not None,**(runtime.world.inventory if runtime.world else {'gold':None,'shards':None,'materials':None}))
    @app.get('/api/earnings')
    def earnings(): return store.earnings()
    @app.get('/api/dungeon/status')
    def dungeon(): return runtime.dungeon()
    @app.get('/api/knowledge/{kind}')
    def knowledge(kind: str):
        if kind not in ('map','monster','boss','dungeon','item','quest','source','game_version'): raise HTTPException(404,'未知实体')
        return {'kind':kind,'source':'OFFICIAL_UNCOLLECTED','items':store.query('SELECT * FROM knowledge WHERE kind=?',(kind,))}
    if (ROOT/'frontend'/'dist').exists(): app.mount('/',StaticFiles(directory=ROOT/'frontend'/'dist',html=True),name='web')
    return app

app = create_app()
