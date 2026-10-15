"""Explicit test configuration only; no database fallback and no cleanup."""
import hashlib
import json
import os
from pathlib import Path
from uuid import uuid4
import pytest
from backend.db_isolation import ROOT, database_path, test_root, record_start

@pytest.fixture(autouse=True)
def release_test_workers(monkeypatch):
    """An assertion timeout must not leave a worker competing with the next test."""
    from backend.runtime import Runtime
    started=[]
    original=Runtime.start
    def start(runtime,*args,**kwargs):
        if runtime not in started:started.append(runtime)
        return original(runtime,*args,**kwargs)
    monkeypatch.setattr(Runtime,'start',start)
    yield
    leaked=[]
    for runtime in started:
        if runtime.thread and runtime.thread.is_alive():
            leaked.append(runtime.run_id)
            runtime.stop_requested=True
            runtime.wake.set()
            with runtime.condition:runtime.condition.notify_all()
            runtime.thread.join(15)
            assert not runtime.thread.is_alive(),'Test Worker did not release: '+str(runtime.run_id)
    assert not leaked,'Test ended before Worker completion: '+str(leaked)

def historical_hashes():
    return {str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest()
            for p in (ROOT/'data').rglob('*') if p.is_file()}

def pytest_configure(config):
    try:
        root = test_root()
        path = database_path()
    except ValueError as exc:
        raise pytest.UsageError(str(exc)) from exc
    root.mkdir(parents=True, exist_ok=True)
    record_start(path)
    config.option.basetemp = str(root/('pytest-tmp-'+uuid4().hex))
    temp = root/'process-temp'
    temp.mkdir(exist_ok=True)
    for key in ('TEMP','TMP','TMPDIR'):
        os.environ[key] = str(temp)
    config._production_hashes = historical_hashes()
    (root/'production-before.json').write_text(json.dumps(config._production_hashes,indent=2))

def pytest_sessionfinish(session, exitstatus):
    after = historical_hashes()
    same = after == session.config._production_hashes
    (test_root()/'production-after.json').write_text(json.dumps({'unchanged':same,'hashes':after},indent=2))
    if not same:
        session.exitstatus = 1
