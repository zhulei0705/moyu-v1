"""只读知识查询；官网事实不参与模拟器动作或资源结算。"""
import hashlib
import json
import sqlite3
import threading
from pathlib import Path
from .store import now

DUNGEON_ID = 'alama'
PARSER_VERSION = 'alama-table-v1.1.1'
SOURCE_TYPES = ('OFFICIAL_WEB','GAME_OBSERVATION','SIMULATOR','MANUAL_REVIEW')


def key(*parts):
    return hashlib.sha256('|'.join(map(str,parts)).encode()).hexdigest()[:24]


def migrate(connection):
    connection.executescript((Path(__file__).parent/'migrations'/'002_official_knowledge.sql').read_text(encoding='utf-8'))
    connection.execute('INSERT OR IGNORE INTO schema_migration VALUES(?,?)',(2,now()))
    connection.commit()


class KnowledgeDB:
    def __init__(self,path):
        from .db_isolation import validate_path
        path = validate_path(path)
        Path(path).parent.mkdir(parents=True,exist_ok=True)
        self.db=sqlite3.connect(str(path),check_same_thread=False,timeout=20)
        self.db.row_factory=sqlite3.Row
        self.lock=threading.RLock()
        self.db.execute('PRAGMA journal_mode=WAL')
        self.db.execute('PRAGMA synchronous=NORMAL')
        migrate(self.db)

    def rows(self,sql,args=()):
        with self.lock: return [dict(row) for row in self.db.execute(sql,args)]

    def facts(self,entity_type=None,entity_id=None,snapshot_id=None):
        where=['f.dungeon_id=?'];args=[DUNGEON_ID]
        for column,value in [('entity_type',entity_type),('entity_id',entity_id),('snapshot_id',snapshot_id)]:
            if value is not None: where.append('f.'+column+'=?');args.append(value)
        rows=self.rows('''SELECT f.*,d.url AS source_url,s.sha256,v.label AS version_label,v.version_number,
          v.current_verified FROM fact_evidence f JOIN source_snapshot s ON s.id=f.snapshot_id
          JOIN data_source d ON d.id=s.source_id JOIN game_version v ON v.id=f.version_id
          WHERE '''+' AND '.join(where)+' ORDER BY f.snapshot_id,f.entity_type,f.entity_id,f.field',args)
        for row in rows: row['normalized_value']=json.loads(row['normalized_value'])
        return rows

    def dungeons(self):
        return [dict(r,verification_status='OFFICIAL_PAGE_EXTRACTED',current_game_status='UNVERIFIED',automatic_execution_allowed=False)
                for r in self.rows('SELECT * FROM dungeon_definition WHERE source_type=?',('OFFICIAL_WEB',))]

    def detail(self):
        entities=self.dungeons()
        if not entities: return None
        return dict(entities[0],facts=self.facts('dungeon'),unknowns=[f for f in self.facts() if f['verification_status']=='UNKNOWN'],
                    versions=self.rows('SELECT * FROM game_version'),mechanics=self.entities('mechanics'),
                    observations=self.rows('SELECT * FROM game_observation WHERE dungeon_id=?',(DUNGEON_ID,)))

    def entities(self,kind):
        queries={
          'stages':('stage','SELECT * FROM dungeon_stage WHERE dungeon_id=? ORDER BY snapshot_id,ordinal'),
          'bosses':('boss','SELECT * FROM dungeon_boss WHERE dungeon_id=? ORDER BY snapshot_id,stage_id'),
          'mechanics':('mechanic','SELECT * FROM dungeon_mechanic WHERE dungeon_id=? ORDER BY snapshot_id'),
          'rewards':('reward','''SELECT r.*,i.name AS item_name,b.name AS boss_name FROM dungeon_reward r
                               JOIN game_item i ON i.id=r.item_id JOIN dungeon_boss b ON b.id=r.boss_id
                               WHERE r.dungeon_id=? ORDER BY r.snapshot_id,b.name,i.name''')}
        entity_type,sql=queries[kind]
        rows=self.rows(sql,(DUNGEON_ID,))
        facts=self.facts(entity_type)
        by_id={}
        for fact in facts: by_id.setdefault(fact['entity_id'],[]).append(fact)
        for row in rows:
            row['facts']=by_id.get(row['id'],[])
            row['verification_status']='OFFICIAL_PAGE_EXTRACTED'
            row['current_game_status']='UNVERIFIED'
            row['automatic_execution_allowed']=False
        return rows

    def sources(self):
        rows=self.rows('SELECT * FROM data_source ORDER BY url')
        for row in rows:
            row['snapshots']=self.rows('''SELECT s.id,s.sha256,s.fetched_at,s.last_seen_at,s.http_status,s.encoding,s.raw_path,
                s.published_at,s.version_id,s.extractor_version,v.label AS version_label,
                (SELECT COUNT(*) FROM fact_evidence f WHERE f.snapshot_id=s.id AND f.verification_status!='UNKNOWN') AS fact_count,
                (SELECT COUNT(*) FROM fact_evidence f WHERE f.snapshot_id=s.id AND f.verification_status='UNKNOWN') AS unknown_count
                FROM source_snapshot s JOIN game_version v ON v.id=s.version_id WHERE source_id=? ORDER BY fetched_at''',(row['id'],))
            row['attempts']=self.rows('SELECT * FROM collection_attempt WHERE source_id=? ORDER BY id DESC LIMIT 30',(row['id'],))
        return rows

    def conflicts(self):
        facts={f['id']:f for f in self.facts()}
        return [dict(row,evidence=[facts[i] for i in json.loads(row['evidence_ids'])]) for row in self.rows('SELECT * FROM data_conflict ORDER BY field')]

    def stats(self):
        facts=self.facts()
        return {'source_type':'OFFICIAL_WEB','sources':len(self.sources()),'snapshots':len(self.rows('SELECT id FROM source_snapshot')),
                'official_page_facts':sum(f['verification_status']=='OFFICIAL_PAGE_EXTRACTED' for f in facts),
                'current_game_verified_facts':0,'unknown_fields':sum(f['verification_status']=='UNKNOWN' for f in facts),
                'conflicts':len(self.conflicts()),'automatic_execution_allowed':False,'parser_version':PARSER_VERSION}


def router(repository):
    from fastapi import APIRouter, HTTPException
    from fastapi.responses import Response
    routes=APIRouter(prefix='/api/knowledge',tags=['只读官网知识'])
    def require(dungeon_id):
        if dungeon_id!=DUNGEON_ID or not repository.dungeons(): raise HTTPException(404,'副本资料尚未采集或不存在')
    @routes.get('/dungeons')
    def dungeons():
        return {'items':repository.dungeons(),'stats':repository.stats(),'facts':repository.facts('dungeon','alama')}
    @routes.get('/dungeons/{dungeon_id}')
    def detail(dungeon_id:str): require(dungeon_id);return repository.detail()
    for kind in ('stages','bosses','rewards','mechanics'):
        def make_endpoint(kind):
            def endpoint(dungeon_id:str):
                require(dungeon_id)
                return {'items':repository.entities(kind),'source_type':'OFFICIAL_WEB','current_game_status':'UNVERIFIED','automatic_execution_allowed':False}
            return endpoint
        routes.add_api_route('/dungeons/{dungeon_id}/'+kind,make_endpoint(kind),methods=['GET'])
    @routes.get('/sources')
    def sources(): return {'items':repository.sources(),'stats':repository.stats()}
    @routes.get('/conflicts')
    def conflicts(): return {'items':repository.conflicts()}
    @routes.get('/snapshots/{snapshot_id}')
    def snapshot(snapshot_id:str):
        rows=repository.rows('SELECT * FROM source_snapshot WHERE id=?',(snapshot_id,))
        if not rows: raise HTTPException(404,'快照不存在')
        row=rows[0]
        # 快照只能作为文件下载，禁止以同源 HTML 执行官网脚本。
        return Response(bytes(row['raw_html']),media_type='application/octet-stream',
                        headers={'Content-Disposition':f'attachment; filename={snapshot_id}.html','X-Content-Type-Options':'nosniff'})
    return routes
