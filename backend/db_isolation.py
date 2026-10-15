"""Fail closed before test connections can touch historical data."""
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PRODUCTION = ROOT / 'data' / 'agent.sqlite3'

def is_test():
    return os.getenv('MOYU_ENV', '').upper() in ('TEST', 'SIMULATOR_TEST') or 'pytest' in sys.modules or bool(os.getenv('PYTEST_CURRENT_TEST'))

def within(path, root):
    return path == root or root in path.parents

def test_root():
    if os.getenv('MOYU_ENV', '').upper() not in ('TEST', 'SIMULATOR_TEST'):
        raise ValueError('DB_ISOLATION: explicit MOYU_ENV=TEST or SIMULATOR_TEST required')
    if not os.getenv('MOYU_DB') or not os.getenv('MOYU_TEST_ROOT'):
        raise ValueError('DB_ISOLATION: explicit MOYU_DB and MOYU_TEST_ROOT required; no production fallback')
    root = Path(os.environ['MOYU_TEST_ROOT']).resolve()
    if not Path(os.environ['MOYU_TEST_ROOT']).is_absolute():
        raise ValueError('DB_ISOLATION: test root must be absolute')
    if root == ROOT / 'reports' or not within(root, ROOT / 'reports'):
        raise ValueError('DB_ISOLATION: dedicated isolated child of reports required')
    return root

def validate_path(path):
    if os.getenv('MOYU_ENV','PRODUCTION').upper() not in ('PRODUCTION','TEST','SIMULATOR_TEST'):
        raise ValueError('DB_ISOLATION: unrecognized execution environment')
    resolved = Path(path).resolve()
    configured = Path(os.environ['MOYU_DB']).resolve() if os.getenv('MOYU_DB') else None
    if is_test() or within(resolved, ROOT/'reports') or (configured and within(configured, ROOT/'reports')):
        root = test_root()
        production = [PRODUCTION.resolve()]
        if os.getenv('MOYU_PRODUCTION_DB'):
            production.append(Path(os.environ['MOYU_PRODUCTION_DB']).resolve())
        for candidate in (resolved, Path(os.environ['MOYU_DB']).resolve()):
            if within(candidate, (ROOT / 'data').resolve()) or not within(candidate, root):
                raise ValueError('DB_ISOLATION: path must stay inside test root and outside data')
            for real in production:
                if candidate == real or (candidate.exists() and real.exists() and os.path.samefile(candidate, real)):
                    raise ValueError('DB_ISOLATION: production/test database path conflict')
    return resolved

def database_path(explicit=None):
    if is_test():
        test_root()
    return validate_path(explicit or os.getenv('MOYU_DB') or PRODUCTION)

def record_start(path):
    if is_test():
        root = test_root()
        root.mkdir(parents=True, exist_ok=True)
        with (root/'database-starts.jsonl').open('a', encoding='utf-8') as out:
            out.write(json.dumps({'utc':datetime.now(timezone.utc).isoformat(), 'pid':os.getpid(),
                'environment':os.environ['MOYU_ENV'], 'database_absolute_path':str(path), 'test_root':str(root)})+'\n')
