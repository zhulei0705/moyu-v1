import {test} from 'node:test'
import assert from 'node:assert/strict'
import ts from 'typescript'
import {readFileSync} from 'node:fs'
const source=readFileSync(new URL('../src/console.ts',import.meta.url),'utf8')
const compiled=ts.transpileModule(source,{compilerOptions:{target:ts.ScriptTarget.ES2022,module:ts.ModuleKind.ES2022}}).outputText
const {metric, bagFree, ratio, calculated, validStatus, request}=await import('data:text/javascript;base64,'+Buffer.from(compiled).toString('base64'))

test('缺失值、非数字、无穷值与真实零值分别显示',()=>{
  for(const value of [null,undefined])assert.equal(metric(value),'待获取')
  for(const value of ['12','',false,NaN,Infinity,-1,{}])assert.equal(metric(value),'暂无有效数据')
  assert.equal(metric(0),'0');assert.equal(metric(2.34,1),'2.3')
})
test('背包空字段、容量为零和异常占用不会产生NaN',()=>{
  assert.equal(bagFree(undefined,undefined),'待获取')
  assert.equal(bagFree(0,0),'0');assert.equal(bagFree(3,4),'暂无有效数据')
  assert.equal(bagFree('12',1),'暂无有效数据');assert.equal(bagFree(12,2),'10')
})
test('耗时、进度、收益效率分母为零或无样本不可计算',()=>{
  assert.equal(ratio(100,0),null);assert.equal(ratio(null,3),null)
  assert.equal(calculated(ratio(100,0)),'暂无有效数据')
  assert.equal(ratio(0,10),0);assert.equal(ratio(100,10,60),600)
})
test('缺失Worker字段不能当作IDLE或启动成功',()=>{
  assert.equal(validStatus({}),false);assert.equal(validStatus(null),false)
  assert.equal(validStatus({running:true,state:'SEARCH',scene_id:'original'}),false)
  assert.equal(validStatus({running:false,paused:false,state:'IDLE',scene_id:'original'}),true)
})
test('正常请求使用同源API、控制头与JSON',async()=>{
  const saved=globalThis.fetch
  try{globalThis.fetch=async(url,options)=>{assert.equal(url,'/api/agent/start');assert.equal(options.headers['X-Agent-Control'],'local-console');return new Response(JSON.stringify({running:true}),{status:200})}
    assert.deepEqual(await request('agent/start',{method:'POST',headers:{'X-Agent-Control':'local-console'}}),{running:true})
  }finally{globalThis.fetch=saved}
})
test('后端不可达转换为中文连接错误',async()=>{
  const saved=globalThis.fetch
  try{globalThis.fetch=async()=>{throw new TypeError('Failed to fetch')};await assert.rejects(request('agent/status'),e=>e.code==='NETWORK'&&e.message.includes('无法连接本地服务'))}finally{globalThis.fetch=saved}
})
test('请求超时会实际中止且不会自动重发',async()=>{
  const saved=globalThis.fetch;let calls=0,aborted=false
  try{globalThis.fetch=async(url,{signal})=>new Promise((resolve,reject)=>{calls++;signal.addEventListener('abort',()=>{aborted=true;reject(new DOMException('aborted','AbortError'))})})
    await assert.rejects(request('agent/start',{method:'POST'},15),e=>e.code==='TIMEOUT');assert.equal(calls,1);assert.equal(aborted,true)
  }finally{globalThis.fetch=saved}
})
test('启动失败与重复启动保留HTTP状态和服务原因',async()=>{
  const saved=globalThis.fetch
  try{for(const code of [409,500]){globalThis.fetch=async()=>new Response(JSON.stringify({detail:code===409?'Agent 已在执行':'场景加载失败'}),{status:code});await assert.rejects(request('agent/start'),e=>e.status===code&&e.code==='HTTP'&&e.message.includes('请求失败'))}}finally{globalThis.fetch=saved}
})
test('无效响应格式显示数据错误',async()=>{
  const saved=globalThis.fetch
  try{globalThis.fetch=async()=>new Response('invalid json');await assert.rejects(request('agent/status'),e=>e.code==='DATA')}finally{globalThis.fetch=saved}
})
