"""Shared guards for native-browser acceptance scripts."""
import os
from pathlib import Path
import httpx
from backend.db_isolation import test_root, validate_path

def report_root():
    root=test_root()/'artifacts'
    root.mkdir(parents=True,exist_ok=True)
    return root

def require_test_service(url):
    health=httpx.get(url+'/api/health',timeout=15)
    health.raise_for_status()
    data=health.json()
    if data.get('execution_environment') not in ('TEST','SIMULATOR_TEST'):
        raise ValueError('DB_ISOLATION: browser controls require explicit test service')
    path=validate_path(data['database_path'])
    if os.getenv('MOYU_SOURCE_SHA256') and data.get('source_sha256')!=os.environ['MOYU_SOURCE_SHA256']:
        raise ValueError('Frozen source mismatch')
    return data
