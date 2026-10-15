import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from backend.store import Store
root=Path(__file__).resolve().parents[1]
(root/'data').mkdir(exist_ok=True)
Store(root/'data'/'agent.sqlite3')
print('SQLite 初始化完成')
