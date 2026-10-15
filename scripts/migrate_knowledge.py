import argparse
import sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from backend.knowledge import KnowledgeDB
parser=argparse.ArgumentParser()
parser.add_argument('--db',type=Path,default=ROOT/'data'/'agent.sqlite3')
args=parser.parse_args()
repo=KnowledgeDB(args.db)
print(repo.rows('SELECT * FROM schema_migration'))
repo.db.close()
