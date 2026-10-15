"""Non-destructive evidence accounting and content-addressed lossless frames."""
import hashlib
import shutil
import time
from collections import deque
from pathlib import Path
import sqlite3
import json
import threading
import math


class Evidence:
    def __init__(self, store, path):
        self.store = store
        self.path = Path(path).resolve()
        self.samples = deque(maxlen=120)
        self.cached = None
        self.sampled = 0
        self.last_archive = None
        self.last_stop = None
        self.monitor_file = self.path.with_suffix(self.path.suffix+'.monitor.json')
        self.monitor_mtime = self.monitor_file.stat().st_mtime_ns if self.monitor_file.exists() else None
        if self.monitor_file.exists():
            try:
                persisted = json.loads(self.monitor_file.read_text(encoding='utf-8'))
                self.last_archive = persisted.get('last_archive')
                self.last_stop = persisted.get('last_safe_stop')
            except (OSError, ValueError):
                self.last_archive = {'status':'FAILED','error':'监控元数据无法读取'}
        self.write_error = None
        self.write_count = 0
        self.created = time.monotonic()
        self.debug_buffer = []
        self.debug_lock = threading.RLock()
        self.debug_sequence = 0
        self.debug_last_capture = 0
        self.recent_debug = deque(maxlen=12)
        self.policy = dict(debug_level='INFO', debug_batch_size=100,
            debug_shard_bytes=4*1024**2, debug_retention_days=None,
            debug_capture_interval_seconds=1, archive_retention_days=None,
            delete_enabled=False)
        self.soft_ratio = .8
        self.disk_reserve = 64 * 1024**2
        policy_file = self.path.with_suffix(self.path.suffix+'.retention.json')
        if policy_file.exists():
            try:
                configured = json.loads(policy_file.read_text(encoding='utf-8-sig'))
            except (OSError, ValueError) as exc:
                raise ValueError(f'容量策略文件 {policy_file} 输入无法读取：{exc}；允许范围：有效 JSON 对象，禁止关闭保护') from exc
            if not isinstance(configured, dict):
                raise ValueError(f'容量策略字段 retention 输入={configured!r}；允许范围：JSON 对象')
            if 'delete_enabled' in configured and configured['delete_enabled'] is not False:
                raise ValueError(f"容量策略字段 delete_enabled 输入={configured['delete_enabled']!r}；允许范围：false，本版本不允许自动删除历史证据")
            for key in self.policy:
                if key in configured:
                    self.policy[key] = configured[key]
            if self.policy['debug_level'] not in ('DEBUG','INFO','WARN','ERROR'):
                raise ValueError('无效调试日志级别')
            for key in ('debug_batch_size','debug_shard_bytes','debug_capture_interval_seconds'):
                if not isinstance(self.policy[key],(int,float)) or self.policy[key] <= 0:
                    raise ValueError('调试策略参数必须为正数')
            limit = configured.get('hard_limit_bytes',store.max_database_bytes)
            reserve = configured.get('disk_reserve_bytes',self.disk_reserve)
            ratio = configured.get('soft_ratio',self.soft_ratio)
            for key, value in (('hard_limit_bytes', limit), ('disk_reserve_bytes', reserve)):
                if type(value) is not int or not 64*1024**2 <= value <= 2**63-1:
                    raise ValueError(f'容量策略字段 {key} 输入={value!r}；允许范围：整数 67108864..9223372036854775807 字节，禁止关闭保护')
            if type(ratio) not in (int, float) or not .5 <= ratio <= .95 or not math.isfinite(ratio):
                raise ValueError(f'容量策略字段 soft_ratio 输入={ratio!r}；允许范围：有限数值 0.5..0.95，禁止关闭保护')
            store.max_database_bytes, self.disk_reserve, self.soft_ratio = limit, reserve, ratio
        store.db.executescript((Path(__file__).parent/'migrations/008_evidence.sql').read_text(encoding='utf-8'))
        from .store import now
        store.db.execute('INSERT OR IGNORE INTO schema_migration VALUES(?,?)',(8,now()))
        store.db.commit()

    def frame(self, observation_id, png):
        digest = hashlib.sha256(png).hexdigest()
        with self.store.lock, self.store.db:
            self.store.db.execute('INSERT OR IGNORE INTO evidence_blob VALUES(?,?)', (digest, png))
            self.store.db.execute('INSERT INTO evidence_frame_ref VALUES(?,?)', (observation_id, digest))
        return digest

    def resolve(self, observation_id):
        with self.store.lock:
            row = self.store.db.execute('''SELECT CASE WHEN length(o.frame_png)>0 THEN o.frame_png ELSE b.png END
              FROM agent_observation o LEFT JOIN evidence_frame_ref r ON r.observation_id=o.id
              LEFT JOIN evidence_blob b ON b.hash=r.hash WHERE o.id=?''', (observation_id,)).fetchone()
        return row[0] if row else None

    def status(self, force=False):
        current = time.monotonic()
        if self.cached and not force and current-self.sampled < 5:
            return dict(self.cached)
        if self.monitor_file.exists() and self.monitor_file.stat().st_mtime_ns != self.monitor_mtime:
            try:
                persisted=json.loads(self.monitor_file.read_text(encoding='utf-8'))
                self.last_archive=persisted.get('last_archive',self.last_archive)
                self.last_stop=persisted.get('last_safe_stop',self.last_stop)
                self.monitor_mtime=self.monitor_file.stat().st_mtime_ns
            except (OSError,ValueError):
                self.last_archive={'status':'FAILED','error':'监控元数据无法读取'}
        try:
            with self.store.lock:
                logical = self.store.db.execute('PRAGMA page_count').fetchone()[0] * self.store.db.execute('PRAGMA page_size').fetchone()[0]
        except sqlite3.Error as exc:
            logical = None
            self.write_error = str(exc)
        physical = {suffix or 'database': Path(str(self.path)+suffix).stat().st_size if Path(str(self.path)+suffix).exists() else 0 for suffix in ('', '-wal', '-shm')}
        media = sum(p.stat().st_size for folder in (self.path.parent/'observation-media', self.path.parent/(self.path.name+'.evidence')) for p in folder.rglob('*') if p.is_file())
        # WAL is a transient duplicate but still consumes real disk and quota.
        total = sum(physical.values()) + media
        if logical is not None:
            self.samples.append((current, logical+media))
        first = self.samples[0] if self.samples else (current,0)
        rate = max(0, (logical+media-first[1])/(current-first[0])) if logical is not None and current>first[0] else 0
        free = shutil.disk_usage(self.path.parent).free
        limit = self.store.max_database_bytes
        soft = total >= limit*self.soft_ratio
        short_rate = max(0,(self.samples[-1][1]-self.samples[-2][1])/(current-self.samples[-2][0])) if len(self.samples)>1 and current>self.samples[-2][0] else 0
        trend = len(self.samples)>=12 and short_rate > max(1024*1024,rate*3)
        alerts = []
        if soft: alerts.append('STORAGE_SOFT_LIMIT')
        if trend: alerts.append('STORAGE_TREND_ANOMALY')
        if self.write_error: alerts.append('DATABASE_WRITE_ERROR')
        if self.last_archive and self.last_archive.get('status')=='FAILED': alerts.append('ARCHIVE_FAILED')
        self.cached = dict(used_bytes=total, logical_bytes=logical, files=physical, media_bytes=media,
            disk_free_bytes=free, bytes_per_minute=rate*60, bytes_per_hour=rate*3600,
            seconds_to_limit=max(0,min(limit-total,free-self.disk_reserve))/rate if rate else None,
            limit_bytes=limit, soft_warning=soft, database_health='WRITE_ERROR' if self.write_error else 'CONNECTED',
            database_write_error=self.write_error, last_archive=self.last_archive, last_safe_stop=self.last_stop,
            measurement_seconds=current-first[0], visual_policy='LOSSLESS_EXACT_DEDUP_ALL_UNIQUE_RETAINED')
        self.cached.update(write_count=self.write_count, writes_per_minute=self.write_count*60/max(.001,current-self.created),
            debug_buffer_rows=len(self.debug_buffer), retention_policy=dict(self.policy),
            alerts=alerts, trend_anomaly=trend,
            debug_capture_interval_seconds=self.policy['debug_capture_interval_seconds']*(8 if soft or trend else 1),
            database_health='WRITE_ERROR' if self.write_error else 'CONNECTED')
        self.sampled = current
        return dict(self.cached)

    def guard(self):
        from .research_scene import SceneFailure
        status = self.status()
        if status['used_bytes'] >= self.store.max_database_bytes or status['disk_free_bytes'] <= self.disk_reserve:
            self.last_stop = 'STORAGE_LIMIT'
            self.persist_monitor()
            raise SceneFailure('STORAGE_LIMIT', '证据数据库达到容量上限或磁盘安全余量达到硬阈值')

    def archive(self, destination):
        """Explicit additive archive; never delete the source or replace a destination."""
        destination = Path(destination).resolve()
        try:
            if destination.exists() or destination == self.path:
                raise ValueError('归档目标必须为新文件')
            destination.touch(exist_ok=False)
            # A separate read connection avoids holding the live Store lock
            # for an entire large backup, keeping SSE/status reads responsive.
            with sqlite3.connect(f'file:{self.path.as_posix()}?mode=ro',uri=True) as source, sqlite3.connect(destination) as target:
                source.backup(target)
                if target.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                    raise ValueError('归档完整性验证失败')
            manifest = {'path':str(destination), 'bytes':destination.stat().st_size,
                'sha256':hashlib.sha256(destination.read_bytes()).hexdigest(), 'integrity':'ok', 'source_deleted':False}
            destination.with_suffix(destination.suffix+'.manifest.json').write_text(json.dumps(manifest), encoding='utf-8')
            self.last_archive = dict(manifest, status='OK')
        except Exception as exc:
            self.last_archive = {'status':'FAILED', 'error':str(exc)}
            self.persist_monitor()
            raise
        self.persist_monitor()
        return self.last_archive

    def persist_monitor(self):
        self.cached = None
        try:
            self.monitor_file.write_text(json.dumps(dict(last_archive=self.last_archive,last_safe_stop=self.last_stop)), encoding='utf-8')
            self.monitor_mtime=self.monitor_file.stat().st_mtime_ns
        except OSError:
            # Safety stop must never depend on writing to a failing disk.
            pass

    def debug(self, level, message, details=None):
        """Only ordinary diagnostic data enters this filtered batch queue."""
        ranks = {'DEBUG':0,'INFO':1,'WARN':2,'ERROR':3}
        if ranks[level] < ranks[self.policy['debug_level']]:
            return False
        with self.debug_lock:
            self.debug_buffer.append(dict(at=time.time(),level=level,message=message,details=details))
            if len(self.debug_buffer) >= self.policy['debug_batch_size'] or level in ('WARN','ERROR'):
                self.flush_debug()
        return True

    def flush_debug(self):
        with self.debug_lock:
            if not self.debug_buffer:
                return
            home = self.path.parent/(self.path.name+'.evidence')
            home.mkdir(exist_ok=True)
            path = home/f'debug-{self.debug_sequence:06d}.jsonl'
            while path.exists() and path.stat().st_size >= self.policy['debug_shard_bytes']:
                self.debug_sequence += 1
                path = home/f'debug-{self.debug_sequence:06d}.jsonl'
            try:
                with path.open('a', encoding='utf-8') as output:
                    output.writelines(json.dumps(row,ensure_ascii=False)+'\n' for row in self.debug_buffer)
                self.debug_buffer.clear()
            except OSError as exc:
                self.last_archive = {'status':'FAILED','error':str(exc)}
                self.persist_monitor()
                # Prevent unbounded memory on a failing filesystem. The error is
                # exposed to monitoring; core evidence never uses this queue.
                self.debug_buffer = self.debug_buffer[-int(self.policy['debug_batch_size']):]
                raise

    def capture_debug(self, png, stage=None, anomaly=False):
        """Frequency applies only to extra debug pictures, never action evidence.

        A bounded pre-event ring is flushed on an anomaly. All action before/after
        frames are stored independently and cannot be filtered by this method.
        """
        current = time.monotonic()
        with self.debug_lock:
            self.recent_debug.append((stage,png))
            interval = 0 if anomaly else self.status()['debug_capture_interval_seconds']
            if not anomaly and current-self.debug_last_capture < interval:
                return False
            home = self.path.parent/(self.path.name+'.evidence')
            home.mkdir(exist_ok=True)
            frames = list(self.recent_debug) if anomaly else [(stage,png)]
            for item_stage, image in frames:
                digest = hashlib.sha256(image).hexdigest()
                path = home/(digest+'.png')
                if not path.exists():
                    path.write_bytes(image)
                self.debug('WARN' if anomaly else 'INFO','debug_frame',dict(hash=digest,stage=item_stage,anomaly=anomaly))
            self.debug_last_capture = current
            return True
