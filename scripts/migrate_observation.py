"""离线迁移工具：必须先停止服务，回退前自动建立 SQLite 一致性归档。"""
import argparse,sqlite3,sys,json
from pathlib import Path
from datetime import datetime,timezone
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
def main():
    p=argparse.ArgumentParser();p.add_argument('--db',type=Path,default=ROOT/'data/agent.sqlite3');p.add_argument('--action',choices=['upgrade','rollback-twin','rollback-observation'],default='upgrade');p.add_argument('--service-stopped',action='store_true',required=True)
    args=p.parse_args();stamp=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ');archive=ROOT/'reports'/('migration-'+stamp);archive.mkdir()
    with sqlite3.connect(args.db) as db,sqlite3.connect(archive/'database.sqlite3') as backup:
        db.backup(backup)
        before={t:db.execute('SELECT COUNT(*) FROM '+t).fetchone()[0] for t in ['runs','logs','fact_evidence']}
        if args.action=='upgrade':
            from backend.observation_store import migrate_observation
            migrate_observation(db)
            db.executescript((ROOT/'backend/migrations/004_twin.sql').read_text(encoding='utf-8-sig'))
            db.execute('INSERT OR IGNORE INTO schema_migration VALUES(?,?)',(4,datetime.now(timezone.utc).isoformat()));db.commit()
        else:
            if db.execute('SELECT 1 FROM schema_migration WHERE version>=5').fetchone():raise ValueError('先使用migrate_research.py回退005/006，再回退观察表；本次备份已保存')
            db.executescript((ROOT/'backend/migrations/004_twin_down.sql').read_text(encoding='utf-8-sig'))
            if args.action=='rollback-observation':db.executescript((ROOT/'backend/migrations/003_observation_down.sql').read_text(encoding='utf-8-sig'))
        after={t:db.execute('SELECT COUNT(*) FROM '+t).fetchone()[0] for t in before}
        assert before==after,'历史数据数量异常'
        report={'action':args.action,'database':str(args.db.resolve()),'archive':str(archive.resolve()),'before':before,'after':after,'integrity':db.execute('PRAGMA integrity_check').fetchone()[0],'media_policy':'受管媒体保留原位，回退不删除文件'}
        (archive/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps(report,ensure_ascii=False))
if __name__=='__main__':main()
