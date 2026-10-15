import argparse,json,os,subprocess,sys,time,uuid,base64
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from scripts.service_common import ROOT,directory,atomic,read,Lease,alive,port_open,health

def status(port):
    home=directory(port);state=read(home/'state.json');observed=health(port)
    result=dict(state,port_listening=port_open(port),supervisor_alive=alive(state.get('supervisor_pid')),server_alive=alive(state.get('server_pid')),identity_verified=bool(observed and observed.get('instance_id')==state.get('instance_id')),health=observed)
    if state.get('state') in ('RUNNING','STARTING','BACKOFF','STOPPING') and not any(result[k] for k in ('port_listening','supervisor_alive','server_alive')):
        result.update(recorded_state=state['state'],state='EXITED_UNCONFIRMED',diagnosis='记录未正常结束且进程不存在；确切原因与退出时刻未知')
    return result

def independent_host(args):
    if os.name!='nt':raise RuntimeError('独立宿主仅支持本地Windows WMI，未修改任何权限')
    line=subprocess.list2cmdline(args).replace("'","''");cwd=str(ROOT).replace("'","''")
    script="$ErrorActionPreference='Stop'; $info=New-CimInstance -ClassName Win32_ProcessStartup -ClientOnly -Property @{ShowWindow=[uint16]0}; $result=Invoke-CimMethod -ClassName Win32_Process -MethodName Create -Arguments @{CommandLine='"+line+"';CurrentDirectory='"+cwd+"';ProcessStartupInformation=$info}; if($result.ReturnValue -ne 0){throw ('WMI拒绝启动，返回码'+$result.ReturnValue)}; $result.ProcessId"
    encoded=base64.b64encode(script.encode('utf-16le')).decode('ascii')
    result=subprocess.run(['powershell.exe','-NoProfile','-NonInteractive','-EncodedCommand',encoded],capture_output=True,text=True,timeout=20)
    if result.returncode:raise RuntimeError('独立宿主启动失败，未绕过或修改权限：'+result.stderr[-500:])
    return int(result.stdout.strip())
def stop(port,timeout=20):
    home=directory(port);s=status(port)
    if not s.get('manager_id'):
        if s['port_listening']:raise RuntimeError('端口是未托管服务，无法核实身份；不会自动终止该进程')
        return {'state':'STOPPED','port':port}
    atomic(home/'stop.json',{'manager_id':s['manager_id'],'requested_at':time.time(),'intentional':True})
    deadline=time.monotonic()+timeout
    while time.monotonic()<deadline:
        s=status(port)
        if not s['supervisor_alive'] and not s['server_alive'] and not s['port_listening']:return status(port)
        time.sleep(.2)
    raise RuntimeError('停止超时，已保留主动停止标志；不会自动拉起或强杀未知进程')
def start(a):
    from backend.db_isolation import database_path
    database_path(a.db)
    home=directory(a.port)
    with Lease(home/'command.lock'):
        s=status(a.port)
        if s['port_listening'] or s['supervisor_alive'] or s['server_alive']:raise RuntimeError('服务或管理进程已在运行，禁止重复启动；请先status核对')
        # Explicit human start cancels a prior stop intent. No background actor clears this marker.
        (home/'stop.json').unlink(missing_ok=True)
        manager_id=str(uuid.uuid4());args=[sys.executable,str(ROOT/'scripts/service_runner.py'),'--port',str(a.port),'--db',str(Path(a.db).resolve()),'--manager-id',manager_id,'--max-restarts',str(a.max_restarts),'--base-delay',str(a.base_delay),'--startup-timeout',str(a.startup_timeout)]
        from backend.db_isolation import is_test, test_root
        if is_test():
            args+=['--environment',os.environ['MOYU_ENV'],'--test-root',str(test_root())]
            if os.getenv('MOYU_SOURCE_SHA256'):args+=['--source-sha',os.environ['MOYU_SOURCE_SHA256']]
        if a.watchdog:args.append('--watchdog')
        args+=['--launch-mode','WINDOWS_WMI' if a.independent_host else 'DETACHED_CHILD']
        if a.independent_host:
            launcher_pid=independent_host(args);child=None
        else:
            with (home/'manager.log').open('a',encoding='utf-8') as output:
                child=subprocess.Popen(args,cwd=ROOT,stdout=output,stderr=output,creationflags=(getattr(subprocess,'DETACHED_PROCESS',0)|getattr(subprocess,'CREATE_NEW_PROCESS_GROUP',0)) if os.name=='nt' else 0,start_new_session=os.name!='nt')
            launcher_pid=child.pid
        deadline=time.monotonic()+a.startup_timeout+3
        while time.monotonic()<deadline:
            s=status(a.port)
            if s.get('manager_id')==manager_id:
                if s.get('state')=='RUNNING' and s['identity_verified']:return s
                if s.get('state') in ('FAILED','STOP_TIMEOUT'):raise RuntimeError(s.get('error','服务启动失败'))
            if (child is not None and child.poll() is not None) or (child is None and not alive(launcher_pid)):raise RuntimeError('管理进程提前退出，请查看manager.log')
            time.sleep(.2)
        atomic(home/'stop.json',{'manager_id':manager_id,'requested_at':time.time(),'intentional':True});raise RuntimeError('启动超时，已取消自动拉起；查看启动日志')
def main():
    p=argparse.ArgumentParser();p.add_argument('command',choices=['start','stop','status','restart']);p.add_argument('--port',type=int,default=8000);p.add_argument('--db',default=str(ROOT/'data/agent.sqlite3'));p.add_argument('--watchdog',action='store_true');p.add_argument('--independent-host',action='store_true');p.add_argument('--max-restarts',type=int,default=3);p.add_argument('--base-delay',type=float,default=2);p.add_argument('--startup-timeout',type=float,default=30);a=p.parse_args()
    if not 1<=a.port<=65535 or not 0<=a.max_restarts<=20 or not .1<=a.base_delay<=30 or not 2<=a.startup_timeout<=120:p.error('端口/重启次数/退避/超时参数超出允许范围')
    try:
        if a.command=='status':result=status(a.port)
        elif a.command=='stop':result=stop(a.port)
        else:
            if a.command=='restart':stop(a.port)
            result=start(a)
        print(json.dumps(result,ensure_ascii=True,indent=2))
    except Exception as exc:print(json.dumps({'error':str(exc)},ensure_ascii=True),file=sys.stderr);sys.exit(1)

if __name__=='__main__':main()
