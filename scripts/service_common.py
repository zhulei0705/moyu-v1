import json,os,socket,time
from pathlib import Path
from urllib.request import urlopen

ROOT=Path(__file__).resolve().parents[1]
def runtime_directory():
    from backend.db_isolation import is_test, test_root
    return test_root()/'runtime' if is_test() else ROOT/'.runtime'

def directory(port):return runtime_directory()/f'service-{port}'
def atomic(path,value):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    temp=path.with_name(path.name+f'.{os.getpid()}.tmp')
    temp.write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding='utf-8')
    # Windows readers can briefly deny rename sharing. Keep the previous valid
    # state and retry for at most 1.7s; persistent access denial is still an error.
    for attempt in range(20):
        try:os.replace(temp,path);return
        except PermissionError:
            if attempt==19:raise
            time.sleep(min(.1,.01*2**attempt))
def read(path):
    try:return json.loads(Path(path).read_text(encoding='utf-8'))
    except (OSError,ValueError):return {}
def alive(pid):
    if not isinstance(pid,int) or pid<=0:return False
    if os.name=='nt':
        import ctypes
        from ctypes import wintypes
        dll=ctypes.WinDLL('kernel32',use_last_error=True)
        dll.OpenProcess.restype=wintypes.HANDLE;dll.OpenProcess.argtypes=[wintypes.DWORD,wintypes.BOOL,wintypes.DWORD]
        dll.GetExitCodeProcess.argtypes=[wintypes.HANDLE,ctypes.POINTER(wintypes.DWORD)];dll.CloseHandle.argtypes=[wintypes.HANDLE]
        handle=dll.OpenProcess(0x1000,False,pid)
        if not handle:return False
        try:
            code=wintypes.DWORD();return bool(dll.GetExitCodeProcess(handle,ctypes.byref(code))) and code.value==259
        finally:dll.CloseHandle(handle)
    try:os.kill(pid,0);return True
    except OSError:return False
def port_open(port):
    try:
        with socket.create_connection(('127.0.0.1',port),timeout=.3):return True
    except OSError:return False
def memory_bytes():
    if os.name!='nt':return None
    try:
        import ctypes
        from ctypes import wintypes
        class Counters(ctypes.Structure):
            _fields_=[('cb',wintypes.DWORD),('PageFaultCount',wintypes.DWORD)]+[(name,ctypes.c_size_t) for name in ('PeakWorkingSetSize','WorkingSetSize','QuotaPeakPagedPoolUsage','QuotaPagedPoolUsage','QuotaPeakNonPagedPoolUsage','QuotaNonPagedPoolUsage','PagefileUsage','PeakPagefileUsage')]
        counters=Counters();counters.cb=ctypes.sizeof(counters)
        kernel=ctypes.WinDLL('kernel32');kernel.GetCurrentProcess.restype=wintypes.HANDLE
        dll=ctypes.WinDLL('psapi');dll.GetProcessMemoryInfo.argtypes=[wintypes.HANDLE,ctypes.POINTER(Counters),wintypes.DWORD]
        return counters.WorkingSetSize if dll.GetProcessMemoryInfo(kernel.GetCurrentProcess(),ctypes.byref(counters),counters.cb) else None
    except (OSError,AttributeError):return None
def health(port):
    try:
        with urlopen(f'http://127.0.0.1:{port}/api/health',timeout=1) as response:return json.load(response)
    except Exception:return {}
class Lease:
    def __init__(self,path):self.path=Path(path);self.file=None
    def __enter__(self):
        self.path.parent.mkdir(parents=True,exist_ok=True);self.file=self.path.open('a+b');self.file.seek(0)
        if self.path.stat().st_size==0:self.file.write(b'0');self.file.flush();self.file.seek(0)
        try:
            if os.name=='nt':
                import msvcrt;msvcrt.locking(self.file.fileno(),msvcrt.LK_NBLCK,1)
            else:
                import fcntl;fcntl.flock(self.file.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
        except OSError:self.file.close();raise RuntimeError('已有管理进程持有启动锁，禁止重复服务')
        return self
    def __exit__(self,*args):
        if os.name=='nt':
            import msvcrt;self.file.seek(0);msvcrt.locking(self.file.fileno(),msvcrt.LK_UNLCK,1)
        self.file.close()
