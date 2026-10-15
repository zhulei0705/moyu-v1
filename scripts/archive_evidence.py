"""Additive, verifiable SQLite/media archive. Never removes source evidence."""
import argparse
import hashlib
import json
import shutil
import sqlite3
import zipfile
from pathlib import Path


def digest(path):
    value=hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda:stream.read(65536),b''):value.update(chunk)
    return value.hexdigest()


def _archive(database, destination):
    database=Path(database).resolve(); destination=Path(destination).resolve()
    if destination.exists():raise ValueError('拒绝覆盖现有归档')
    snapshot=destination.parent/(destination.stem+'.snapshot')
    folders=[database.parent/'observation-media',database.parent/(database.name+'.evidence')]
    if any(snapshot.is_relative_to(folder) for folder in folders):
        raise ValueError('归档不能位于待归档媒体目录内')
    snapshot.mkdir(parents=True,exist_ok=False)
    target=snapshot/'database.sqlite3'
    with sqlite3.connect(f'file:{database.as_posix()}?mode=ro',uri=True) as source,sqlite3.connect(target) as db:
        source.execute('PRAGMA cache_size=-65536'); db.execute('PRAGMA cache_size=-65536')
        source.backup(db)
        if db.execute('PRAGMA integrity_check').fetchone()[0]!='ok':raise ValueError('数据库归档完整性失败')
        if db.execute('PRAGMA foreign_key_check').fetchall():raise ValueError('数据库归档引用完整性失败')
    entries=[dict(source=str(database),path='database.sqlite3',bytes=target.stat().st_size,sha256=digest(target))]
    for folder in folders:
        for path in folder.rglob('*'):
            if path.is_file():
                relative=Path(folder.name)/path.relative_to(folder)
                copy=snapshot/relative; copy.parent.mkdir(parents=True,exist_ok=True)
                shutil.copyfile(path,copy)
                entries.append(dict(source=str(path),path=relative.as_posix(),bytes=copy.stat().st_size,sha256=digest(copy)))
    manifest=dict(source=str(database),integrity='ok',source_deleted=False,entries=entries,
        restore_note='Restore files using source/path mapping or rewrite external-media paths in an isolated restore database before any authorized cleanup.')
    (snapshot/'manifest.json').write_text(json.dumps(manifest,indent=2),encoding='utf-8')
    with zipfile.ZipFile(destination,'x',zipfile.ZIP_DEFLATED,compresslevel=6) as bundle:
        for path in snapshot.rglob('*'):
            if path.is_file():bundle.write(path,path.relative_to(snapshot).as_posix())
    with zipfile.ZipFile(destination) as bundle:
        if bundle.testzip() is not None:raise ValueError('归档CRC验证失败')
        for entry in entries:
            value=hashlib.sha256()
            with bundle.open(entry['path']) as stream:
                for chunk in iter(lambda:stream.read(65536),b''):value.update(chunk)
            if value.hexdigest()!=entry['sha256']:raise ValueError('归档内容哈希验证失败')
    result=dict(status='OK',path=str(destination),bytes=destination.stat().st_size,
        sha256=digest(destination),snapshot_path=str(snapshot),manifest=manifest)
    destination.with_suffix(destination.suffix+'.manifest.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
    monitor=database.with_suffix(database.suffix+'.monitor.json')
    state=json.loads(monitor.read_text(encoding='utf-8')) if monitor.exists() else {}
    state['last_archive']={key:value for key,value in result.items() if key!='manifest'}
    monitor.write_text(json.dumps(state),encoding='utf-8')
    return result


def archive(database, destination):
    try:
        return _archive(database,destination)
    except Exception as exc:
        monitor=Path(database).resolve().with_suffix(Path(database).suffix+'.monitor.json')
        try:
            state=json.loads(monitor.read_text(encoding='utf-8')) if monitor.exists() else {}
            state['last_archive']=dict(status='FAILED',path=str(Path(destination).resolve()),error=str(exc),source_deleted=False)
            monitor.write_text(json.dumps(state),encoding='utf-8')
        except (OSError,ValueError):
            # The CLI still returns an error if the failed disk also prevents
            # persisting the warning. Never replace or delete source evidence.
            pass
        raise


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--db',required=True);parser.add_argument('--destination',required=True)
    args=parser.parse_args()
    result=archive(args.db,args.destination)
    print(json.dumps({k:v for k,v in result.items() if k!='manifest'}))
