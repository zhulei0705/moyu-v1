"""Capacity config regression: isolated databases and synthetic capacity only."""
import json
from types import SimpleNamespace
import pytest
from backend.store import Store
from backend.research_scene import SceneFailure
from backend.db_isolation import PRODUCTION

MIB = 1024**2
GIB = 1024**3


def open_store(tmp_path, policy=None):
    path = tmp_path/'capacity.sqlite3'
    if policy is not None:
        path.with_suffix('.sqlite3.retention.json').write_text(json.dumps(policy), encoding='utf-8-sig')
    return Store(path)


def test_defaults_without_policy(tmp_path):
    s = open_store(tmp_path)
    try:
        assert (s.max_database_bytes, s.evidence.disk_reserve, s.evidence.soft_ratio) == (2*GIB, 64*MIB, .8)
        assert s.evidence.path != PRODUCTION.resolve()
    finally:
        s.db.close()


@pytest.mark.parametrize('policy,reserve', [({}, 64*MIB),
    ({'hard_limit_bytes':7*GIB, 'disk_reserve_bytes':128*MIB, 'soft_ratio':.65, 'delete_enabled':False},128*MIB)])
def test_valid_policy_and_missing_reserve(tmp_path, policy, reserve):
    s = open_store(tmp_path, policy)
    try:
        assert s.evidence.disk_reserve == reserve
        assert s.max_database_bytes == policy.get('hard_limit_bytes',2*GIB)
        assert s.evidence.soft_ratio == policy.get('soft_ratio',.8)
        assert s.evidence.status(force=True)['limit_bytes'] == s.max_database_bytes
        assert 7*GIB == 7516192768
    finally:
        s.db.close()


@pytest.mark.parametrize('field,values', [
    ('disk_reserve_bytes', [None,'7 GiB','67108864',-1,0,64*MIB-1,True,64*MIB+.5,2**63]),
    ('hard_limit_bytes', [None,'7 GiB',-1,0,64*MIB-1,True,2**63]),
    ('soft_ratio', [None,'0.8',-1,0,.49,.951,True,float('nan'),float('inf'),10**400]),
    ('delete_enabled', [True,None,0,'false'])])
def test_invalid_configuration_refuses_start(tmp_path, field, values):
    from backend.main import create_app
    for i, value in enumerate(values):
        path = tmp_path/f'invalid-{i}.sqlite3'
        path.with_suffix('.sqlite3.retention.json').write_text(json.dumps({field:value}),encoding='utf-8')
        with pytest.raises(ValueError) as error:
            create_app(path)
        message = str(error.value)
        assert field in message and repr(value) in message and '允许范围' in message


@pytest.mark.parametrize('value', [64*MIB,2**63-1])
def test_byte_boundaries(tmp_path,value):
    s = open_store(tmp_path,{'disk_reserve_bytes':value,'hard_limit_bytes':value})
    try:
        assert s.evidence.disk_reserve == s.max_database_bytes == value
    finally:
        s.db.close()


@pytest.mark.parametrize('ratio',[.5,.95])
def test_ratio_boundaries(tmp_path,ratio):
    s = open_store(tmp_path,{'soft_ratio':ratio})
    try:
        assert s.evidence.soft_ratio == ratio
    finally:
        s.db.close()


@pytest.mark.parametrize('raw',['null','[]','{"disk_reserve_bytes":','false'])
def test_malformed_policy_refuses_start(tmp_path,raw):
    path=tmp_path/'bad.sqlite3'
    path.with_suffix('.sqlite3.retention.json').write_text(raw,encoding='utf-8')
    with pytest.raises(ValueError,match='允许范围'):
        Store(path)


def test_separate_soft_hard_and_reserve_thresholds(tmp_path,monkeypatch):
    s = open_store(tmp_path,{'hard_limit_bytes':7*GIB,'disk_reserve_bytes':128*MIB,'soft_ratio':.75})
    e = s.evidence
    free = 22*GIB
    used = 0
    # Override stat sizes only for this database, never create large files.
    original_stat = type(e.path).stat
    def stat(path,*args,**kwargs):
        result=original_stat(path,*args,**kwargs)
        return SimpleNamespace(st_size=used) if path == e.path else result
    monkeypatch.setattr(type(e.path),'stat',stat)
    monkeypatch.setattr('backend.evidence.shutil.disk_usage',lambda _:SimpleNamespace(free=free))
    try:
        s.db.execute('PRAGMA wal_checkpoint(TRUNCATE)')
        overhead = sum(e.status(force=True)['files'][k] for k in ('-wal','-shm'))
        used=int(7*GIB*.75)-overhead-1
        assert not e.status(force=True)['soft_warning']
        used+=1
        assert 'STORAGE_SOFT_LIMIT' in e.status(force=True)['alerts']
        e.guard()  # soft warning does not stop
        used=7*GIB-overhead-1
        e.status(force=True);e.guard()
        used+=1
        e.status(force=True)
        with pytest.raises(SceneFailure):e.guard()
        assert e.last_stop == 'STORAGE_LIMIT'
        used=0;free=128*MIB+1
        e.status(force=True);e.guard()
        free-=1
        assert e.status(force=True)['used_bytes'] < 7*GIB
        with pytest.raises(SceneFailure):e.guard()
        assert e.disk_reserve == 128*MIB and s.max_database_bytes == 7*GIB
    finally:
        s.db.close()


def test_capacity_environment_does_not_override_persisted_policy(tmp_path,monkeypatch):
    monkeypatch.setenv('MOYU_DISK_RESERVE_BYTES','0')
    s=open_store(tmp_path,{'disk_reserve_bytes':128*MIB})
    try:
        assert s.evidence.disk_reserve == 128*MIB
        with pytest.raises(ValueError,match='DB_ISOLATION'):
            Store(PRODUCTION)
    finally:
        s.db.close()
