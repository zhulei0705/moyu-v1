import os
import subprocess
import sys
from pathlib import Path
import pytest
from backend.db_isolation import PRODUCTION, database_path, validate_path

@pytest.mark.parametrize('key',['MOYU_ENV','MOYU_DB','MOYU_TEST_ROOT'])
def test_missing_configuration_refused(monkeypatch,key):
    monkeypatch.delenv(key,raising=False)
    with pytest.raises(ValueError,match='DB_ISOLATION'):
        database_path()

def test_production_and_traversal_refused(tmp_path,monkeypatch):
    for path in (PRODUCTION,Path(os.environ['MOYU_TEST_ROOT'])/'..'/'outside.sqlite3'):
        with pytest.raises(ValueError,match='DB_ISOLATION'):
            validate_path(path)
    monkeypatch.setenv('MOYU_DB',str(PRODUCTION))
    with pytest.raises(ValueError,match='DB_ISOLATION'):
        database_path(tmp_path/'safe.sqlite3')

def test_hardlink_conflict_refused(tmp_path,monkeypatch):
    source=tmp_path/'standin-production.sqlite3'
    source.write_bytes(b'historical')
    link=tmp_path/'test.sqlite3'
    os.link(source,link)
    monkeypatch.setenv('MOYU_PRODUCTION_DB',str(source))
    with pytest.raises(ValueError,match='conflict'):
        validate_path(link)
    assert source.read_bytes()==b'historical'

def test_app_import_without_db_never_falls_back(tmp_path):
    env=dict(os.environ,MOYU_ENV='TEST')
    env.pop('MOYU_DB',None)
    result=subprocess.run([sys.executable,'-c','import backend.main'],env=env,capture_output=True,text=True,timeout=20)
    assert result.returncode!=0 and 'DB_ISOLATION' in result.stderr

def test_report_database_without_test_environment_is_refused(tmp_path):
    env=dict(os.environ,MOYU_DB=str(tmp_path/'unmarked.sqlite3'))
    for key in ('MOYU_ENV','MOYU_TEST_ROOT','PYTEST_CURRENT_TEST'):
        env.pop(key,None)
    result=subprocess.run([sys.executable,'-c','import backend.main'],env=env,capture_output=True,text=True,timeout=20)
    assert result.returncode!=0 and 'DB_ISOLATION' in result.stderr
    assert not (tmp_path/'unmarked.sqlite3').exists()

def test_direct_repository_and_service_refuse_production(tmp_path):
    from backend.store import Store
    from backend.knowledge import KnowledgeDB
    from backend.observation_store import ObservationStore
    for factory in (lambda:Store(PRODUCTION),lambda:KnowledgeDB(PRODUCTION),lambda:ObservationStore(PRODUCTION,tmp_path)):
        with pytest.raises(ValueError,match='DB_ISOLATION'):
            factory()
    result=subprocess.run([sys.executable,'scripts/service_manager.py','start','--port','8046','--db',str(PRODUCTION)],capture_output=True,text=True,timeout=20)
    assert result.returncode!=0 and 'DB_ISOLATION' in result.stderr

def test_explicit_database_is_recorded(tmp_path):
    from backend.main import create_app
    app=create_app(tmp_path/'explicit.sqlite3')
    assert database_path(tmp_path/'explicit.sqlite3')==tmp_path/'explicit.sqlite3'
    assert (Path(os.environ['MOYU_TEST_ROOT'])/'database-starts.jsonl').exists()
    for repository in (app.state.store,app.state.knowledge,app.state.observation):
        repository.db.close()
