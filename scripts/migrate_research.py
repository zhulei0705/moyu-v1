"""停服后升级/回退V1.3：先一致性归档全部数据库，再只操作新增研究表。"""
import argparse,sqlite3,json,hashlib
from pathlib import Path
from datetime import datetime,timezone
ROOT=Path(__file__).resolve().parents[1]
def main():
    p=argparse.ArgumentParser();p.add_argument('--db',type=Path,default=ROOT/'data/agent.sqlite3');p.add_argument('--action',choices=['upgrade','rollback'],default='upgrade');p.add_argument('--service-stopped',action='store_true',required=True);args=p.parse_args()
    if not args.db.is_file():raise ValueError('数据库不存在，不能重建代替迁移')
    out=ROOT/'reports'/('research-migration-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ'));out.mkdir()
    with sqlite3.connect(args.db) as db,sqlite3.connect(out/'database.sqlite3') as backup:
        db.backup(backup);db.execute('PRAGMA foreign_keys=ON')
        if not db.execute('SELECT 1 FROM schema_migration WHERE version=4').fetchone():raise ValueError('要求V1.2迁移版本4')
        tables=['runs','logs','fact_evidence','observation_media','observation','twin_scene_revision']
        def hashes():return {t:hashlib.sha256(repr(db.execute('SELECT * FROM '+t+' ORDER BY rowid').fetchall()).encode()).hexdigest() for t in tables}
        before=hashes()
        names=['005_research.sql','006_research_settlement.sql'] if args.action=='upgrade' else ['006_research_settlement_down.sql','005_research_down.sql']
        for name in names:db.executescript((ROOT/'backend/migrations'/name).read_text(encoding='utf-8-sig'))
        if args.action=='upgrade':
            for version in (5,6):db.execute('INSERT OR IGNORE INTO schema_migration VALUES(?,?)',(version,datetime.now(timezone.utc).isoformat()))
            db.commit()
        after=hashes();assert before==after
        report={'action':args.action,'database':str(args.db.resolve()),'archive':str(out.resolve()),'protected_hashes_before':before,'protected_hashes_after':after,'integrity':db.execute('PRAGMA integrity_check').fetchone()[0]}
        (out/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps(report,ensure_ascii=False))
if __name__=='__main__':main()
