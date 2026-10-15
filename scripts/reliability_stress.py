import argparse,asyncio,json,time,subprocess,sys,sqlite3
from pathlib import Path
import httpx
ROOT=Path(__file__).resolve().parents[1]

async def main():
    parser=argparse.ArgumentParser();parser.add_argument('--url',default='http://127.0.0.1:8012');parser.add_argument('--rounds',type=int,default=8);a=parser.parse_args()
    checks=[];samples=[];counts=[0]*6;stopping=asyncio.Event();start=time.monotonic()
    def check(name,result,details=None):checks.append({'name':name,'passed':bool(result),'details':details});assert result,name
    async with httpx.AsyncClient(base_url=a.url,timeout=15) as api:
        async def get(path):r=await api.get('/api/'+path);r.raise_for_status();return r.json()
        previous={r['id'] for r in await get('runs')};baseline=await get('research/earnings')
        async def reader(index):
            async with httpx.AsyncClient(timeout=None) as stream_client:
                async with stream_client.stream('GET',a.url+'/api/logs/stream?after=0') as response:
                    response.raise_for_status()
                    async for line in response.aiter_lines():
                        if line.startswith('id:'):counts[index]+=1
                        if stopping.is_set():return
        tasks=[asyncio.create_task(reader(i)) for i in range(6)]
        response=await api.post('/api/agent/start',json={'rounds':a.rounds,'scene_id':'NineStarResearchDungeon','seed':123},headers={'X-Agent-Control':'local-console'});response.raise_for_status()
        try:
            while time.monotonic()-start<300:
                state=await get('agent/status');samples.append(await get('service/status'))
                if not state['running']:break
                await asyncio.sleep(.5)
            check('连续九星模拟轮次完成',not state['running'] and state['completedSession']==a.rounds,{'completed':state['completedSession'],'rounds':a.rounds})
            check('六个SSE长连接真实并发',max(s['sse_clients'] for s in samples)>=6)
            check('SSE大量真实历史补读',all(c>1000 for c in counts),counts)
        finally:
            stopping.set()
            for task in tasks:task.cancel()
            await asyncio.gather(*tasks,return_exceptions=True)
        await asyncio.sleep(1)
        after=await get('service/status');check('关闭SSE后连接计数释放',after['sse_clients']==0)
        rows=[r for r in await get('runs') if r['id'] not in previous];check('每轮唯一run_id且真实完成',len(rows)==a.rounds and len({r['id'] for r in rows})==a.rounds and all(r['status']=='COMPLETED' for r in rows))
        earnings=await get('research/earnings');check('结算与完成轮次一致',earnings['completed']-baseline['completed']==a.rounds)
        check('实际心跳持续更新',samples[-1]['heartbeat_at']>samples[0]['heartbeat_at'])
        result={'passed':all(c['passed'] for c in checks),'checks':checks,'duration_seconds':time.monotonic()-start,'rounds':a.rounds,'run_ids':[r['id'] for r in rows],'sse_events_received':counts,'samples':samples,'after_cleanup':after,'earnings':earnings}
        (ROOT/'reports/v132-stress.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
        print(json.dumps({'passed':True,'rounds':a.rounds,'seconds':result['duration_seconds'],'checks':len(checks)}))
if __name__=='__main__':asyncio.run(main())
