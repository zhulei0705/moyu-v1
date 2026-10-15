import argparse
import json
import sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from backend.knowledge import KnowledgeDB
from backend.collector import Collector

def main():
    parser=argparse.ArgumentParser(description='仅采集指定的三页阿拉玛官方公开资料')
    parser.add_argument('--db',type=Path,default=ROOT/'data'/'agent.sqlite3')
    parser.add_argument('--output',type=Path,default=ROOT/'data'/'official-snapshots')
    parser.add_argument('--report',type=Path,default=ROOT/'reports'/'official-collection.json')
    args=parser.parse_args()
    repo=KnowledgeDB(args.db)
    collector=Collector(repo,args.output)
    try:
        result=collector.collect_all()
        args.report.parent.mkdir(parents=True,exist_ok=True)
        args.report.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
        print(json.dumps(result,ensure_ascii=False,indent=2))
        return int(any(r['status']=='FAILED' for r in result['results']))
    finally:
        collector.client.close()
        repo.db.close()

if __name__=='__main__': raise SystemExit(main())
