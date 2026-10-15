<script setup lang="ts">
import { ref, computed, onMounted, onUnmounted, nextTick, watch } from 'vue'
import {request, ApiError, metric, numberValue, bagFree, ratio, calculated, validStatus} from './console'
const props=defineProps<{page?:string}>()
const scene=ref(props.page==='#/research'?'NineStarResearchDungeon':'original'),scenes=ref<any[]>([]),decisions=ref<any[]>([]),researchEarnings=ref<any>({}),replayDecisions=ref<any[]>([]),decisionIndex=ref(0)
const researchMode=computed(()=>scene.value==='NineStarResearchDungeon'||(status.value.running&&status.value.scene_id==='NineStarResearchDungeon'))
const displayEarnings=computed(()=>{
  const e=researchEarnings.value, successes=(Array.isArray(e.items)?e.items:[]).filter((r:any)=>r?.success)
  const total=successes.length && successes.every((r:any)=>numberValue(r.duration)!==null) ? successes.reduce((n:number,r:any)=>n+r.duration,0) : null
  return researchMode.value?{gold:e.simulated_gold,shards:e.simulated_fragments,materials:e.simulated_materials,completed:e.completed,failed:e.failed,averageSeconds:successes.length===e.completed?ratio(total,e.completed):null}:earnings.value
})
const averageTime=computed(()=>numberValue(displayEarnings.value.completed)!==null && displayEarnings.value.completed>0?calculated(displayEarnings.value.averageSeconds):'暂无有效数据')
const efficiency=computed(()=>{
  const e=displayEarnings.value,a=numberValue(e.averageSeconds),c=numberValue(e.completed)
  return calculated(a!==null&&c!==null?ratio(e.gold,a*c,60):null)
})
const shownRuns=computed(()=>researchMode.value?runs.value.filter(r=>r.scene_id==='NineStarResearchDungeon'):runs.value)
watch(()=>props.page,()=>{if(!status.value.running)scene.value=props.page==='#/research'?'NineStarResearchDungeon':'original'})
type Log = {id:number;timestamp:string;sequence:number;runId:string;stage:string;level:string;eventType:string;message:string;details:Record<string,unknown>}
const appVersion = ref<string|null>(null)
const status = ref<any>({}), dungeon = ref<any>({}), earnings = ref<any>({}), inventory = ref<any>({}), runs = ref<any[]>([])
const logs = ref<Log[]>([]), level = ref(''), search = ref(''), scroll = ref(true), logBox = ref<HTMLElement>()
const rounds = ref(2), seed = ref(1), fault = ref(''), busy = ref(false), error = ref('')
const apiConnected=ref(false),statusKnown=ref(false),syncError=ref(''),dataError=ref(''),sseState=ref('连接中'),lastConnected=ref<string|null>(null)
const connection=computed(()=>`${apiConnected.value?'API 已连接':'API 未连接'} · SSE ${sseState.value}`)
const canControl=computed(()=>apiConnected.value&&statusKnown.value&&!busy.value)
const bagCurrent=computed(()=>dungeon.value.scene_id===scene.value&&dungeon.value.loaded!==false)
const actualScene=computed(()=>dungeon.value.loaded===false?'待创建实际场景':scenes.value.find(s=>s.id===dungeon.value.scene_id)?.name??'待获取')
const frame = ref('/api/frame/latest'), selected = ref<any>(null), replay = ref(0), playing = ref(false)
const expandedLogs=ref(new Set<number>()),expandedDecisions=ref(new Set<string>())
function expandLog(e:Event,id:number){const next=new Set(expandedLogs.value);(e.target as HTMLDetailsElement).open?next.add(id):next.delete(id);expandedLogs.value=next}
function expandDecision(e:Event,id:string){const next=new Set(expandedDecisions.value);(e.target as HTMLDetailsElement).open?next.add(id):next.delete(id);expandedDecisions.value=next}
let pendingLogs:Log[]=[],logTimer:ReturnType<typeof setInterval>,refreshing=false,disposed=false,bootstrapped=false,statusRequest=0
let stream:EventSource, timer:ReturnType<typeof setInterval>, replayTimer:ReturnType<typeof setInterval>
const filtered = computed(()=>logs.value.filter(l=>(!level.value || l.level===level.value)&&(!search.value || (l.message+JSON.stringify(l.details)).includes(search.value))))
const progress = computed(()=>{
  if(dungeon.value.loaded===false)return null
  if(dungeon.value.exited===true)return 100
  const stage=numberValue(dungeon.value.stage), remaining=numberValue(dungeon.value.bossRemaining)
  return stage===null||remaining===null?null:Math.min(100,Math.max(0,(stage-1)*25+(2-remaining)*10))
})
const get=(path:string)=>request(path)
function message(e:unknown){return e instanceof Error?e.message:'操作失败，请重新同步状态。'}
async function readStatus(){
  const order=++statusRequest
  try{const a=await get('agent/status');if(!validStatus(a))throw new ApiError('Worker 状态字段无效，暂不能执行控制，请重新同步。','DATA');if(disposed||order!==statusRequest)return a
    status.value=a;statusKnown.value=true;apiConnected.value=true;lastConnected.value=new Date().toLocaleString();syncError.value='';if(a.running)scene.value=a.scene_id;return a
  }catch(e){if(!disposed&&order===statusRequest){apiConnected.value=false;syncError.value=message(e)}throw e}
}
async function refresh(){
  if(refreshing||disposed)return;refreshing=true
  try{
    const a=await readStatus()
    const jobs:[string,(value:any)=>void][]=[['dungeon/status',v=>dungeon.value=v??{}],['earnings',v=>earnings.value=v??{}],['inventory',v=>inventory.value=v??{}],['runs',v=>runs.value=Array.isArray(v)?v:[]]]
    jobs.push(['health',v=>appVersion.value=typeof v?.version==='string'?v.version:null])
    if(!bootstrapped){jobs.push(['scenes',v=>scenes.value=Array.isArray(v?.items)?v.items:[]],['logs?limit=500&tail=true',v=>{if(Array.isArray(v))logs.value=v.slice(-500)}])}
    if(researchMode.value)jobs.push(['research/decisions?limit=20'+(a.runId?'&run_id='+a.runId:''),v=>decisions.value=Array.isArray(v?.items)?v.items:[]],['research/earnings',v=>researchEarnings.value=v??{}])
    const results=await Promise.allSettled(jobs.map(async([path,assign])=>{const v=await get(path);if(!disposed)assign(v)}))
    if(disposed)return
    const failed=results.find(r=>r.status==='rejected');dataError.value=failed?.status==='rejected'?'部分数据未更新：'+message(failed.reason):''
    if(!failed){bootstrapped=true;if(!stream)connectStream();frame.value='/api/frame/latest?t='+Date.now()}
  }catch{/* readStatus publishes connection error; retry timer stays active */}finally{refreshing=false}
}
async function retry(){error.value='';await refresh()}
async function control(action:string){
  if(busy.value)return;busy.value=true;error.value=''
  try{
    const before=await readStatus()
    if(action==='start'&&before.running)throw new Error('Agent 已在执行，禁止重复启动；已同步实际状态。')
    if(action!=='start'&&!before.running)throw new Error('当前没有执行中的 Agent；已同步实际状态。')
    const result=await request('agent/'+action,{method:'POST',headers:{'Content-Type':'application/json','X-Agent-Control':'local-console'},body:action==='start'?JSON.stringify({rounds:rounds.value,seed:seed.value,fault:fault.value||null,scene_id:scene.value}):undefined},10000)
    const actual=await readStatus()
    const verified=action==='start'?actual.running&&!!actual.runId&&actual.runId===result.runId:action==='pause'?actual.running&&actual.paused:action==='resume'?actual.running&&!actual.paused:!actual.running
    if(!verified)throw new Error(actual.error||'控制请求已返回，但 Worker 未达到预期状态；请重新同步确认，勿重复启动。')
    await refresh()
  }catch(e){error.value=message(e);try{await readStatus()}catch{/* never retry a POST automatically */}}finally{busy.value=false}
}
async function inspect(id:string){try{selected.value=await get('runs/'+id);replay.value=0;playing.value=false;replayDecisions.value=(await get('research/decisions?run_id='+id+'&limit=1000')).items.reverse();decisionIndex.value=0}catch(e){error.value=message(e)}}
function toggleReplay(){playing.value=!playing.value}
function connectStream(){
  stream=new EventSource('/api/logs/stream?after='+(logs.value.at(-1)?.id||0))
  stream.onopen=()=>sseState.value='实时连接';stream.onerror=()=>sseState.value='断线重连中'
  stream.onmessage=event=>{try{const row=JSON.parse(event.data);if(!Number.isInteger(row.id)||typeof row.message!=='string')throw new Error('invalid log');pendingLogs.push(row);if(pendingLogs.length>500)pendingLogs.splice(0,pendingLogs.length-500)}catch{sseState.value='日志数据无效'}}
}
onMounted(()=>{
  timer=setInterval(refresh,800)
  logTimer=setInterval(async()=>{if(!pendingLogs.length)return;const rows=new Map(logs.value.map(l=>[l.id,l]));for(const row of pendingLogs.splice(0))rows.set(row.id,row);logs.value=[...rows.values()].sort((a,b)=>a.id-b.id).slice(-500);if(scroll.value){await nextTick();logBox.value?.scrollTo(0,logBox.value.scrollHeight)}},100)
  replayTimer=setInterval(()=>{if(playing.value&&selected.value){if(replay.value<selected.value.events.length-1)replay.value++;else playing.value=false}},300)
  void refresh()
})
onUnmounted(()=>{disposed=true;stream?.close();clearInterval(timer);clearInterval(logTimer);clearInterval(replayTimer)})
</script>

<template>
  <div class="shell">
    <aside><div class="logo">M<span> / </span>A</div><h3>魔域智能 Agent</h3><small>LOCAL LAB · {{appVersion ? 'V'+appVersion : '待获取版本'}}</small><nav><a class="active" href="/#/console">◈ 运行控制台</a><a href="/#/knowledge">◇ 九星副本知识库</a><a href="/#/flow">⇢ 九星副本流程</a><a href="/#/sources">▤ 数据来源管理</a><a href="/#/observations">◎ 场景观察中心</a><a href="/#/research">◆ 九星副本运行</a><a href="#history">◷ 历史任务</a><a href="/api/logs/export">⇩ 导出 JSONL</a></nav><div class="aside-note"><b>SIMULATED</b><p>原创测试副本<br>本地视觉决策<br>模拟资源收益</p></div></aside>
    <main><header><div><div class="eyebrow">AUTONOMOUS DUNGEON LAB</div><h1>{{researchMode?'九星副本运行':'游戏 Agent 控制台'}}</h1><p>{{researchMode?'九星研究副本 · 历史名称与模拟规则分离':'观察 → 决策 → 动作 → 验证 · 四阶段原创模拟副本'}}</p></div><span class="badge">● {{ connection }}</span></header>
      <div v-if="researchMode && status.storage" class="panel storage-health">
        <h2>长期运行容量</h2>
        <p>运行 {{ calculated(status.elapsedSeconds) }} 秒 · 已完成 {{ status.completedSession }} 轮 · 阶段 {{ dungeon.stage ?? '待获取' }}</p>
        <p>证据 {{ calculated(status.storage.used_bytes / 1048576) }} MiB · 增长 {{ calculated(status.storage.bytes_per_minute / 1048576) }} MiB/分钟 · 磁盘剩余 {{ calculated(status.storage.disk_free_bytes / 1073741824) }} GiB</p>
        <p>预计到达限制 {{ status.storage.seconds_to_limit === null ? '采样尚不足' : calculated(status.storage.seconds_to_limit / 3600) + ' 小时' }} · 数据库 {{ status.storage.database_health }} · {{ status.storage.soft_warning ? '容量软阈值告警' : '容量正常' }}</p>
        <p>最近归档 {{ status.storage.last_archive?.status ?? '尚未归档' }} · 最近安全停止 {{ status.storage.last_safe_stop ?? '无' }}</p>
      </div>
      <div class="notice">运行环境 SIMULATOR · 收益 SIMULATED · 正式版公开资料在只读知识库单独查询。授权画面导入、人工审核与九星研究配置在场景观察中心。</div>
      <p class="actual-scene">画面与本轮指标所属场景：{{actualScene}} · 执行场景选择用于下次启动</p>
      <div class="metrics"><article><label>模拟金币</label><strong>{{ metric(displayEarnings.gold) }}</strong><small>已结算累计</small></article><article><label>模拟碎片 / 材料</label><strong>{{ metric(displayEarnings.shards) }} <i>/ {{ metric(displayEarnings.materials) }}</i></strong><small>背包拾取事件结算</small></article><article><label>完成 / 失败</label><strong>{{ metric(displayEarnings.completed) }} <i>/ {{ metric(displayEarnings.failed) }}</i></strong><small>真实任务记录</small></article><article><label>平均通关耗时</label><strong>{{ averageTime }}<i> 秒</i></strong><small>已完成副本均值 · 收益效率 {{efficiency}} 模拟金币/分钟</small></article></div>
      <section class="workspace"><div class="panel scene"><div class="panel-title"><h2>实时模拟画面</h2><span>OpenCV · {{dungeon.width?dungeon.width*24:480}} × {{dungeon.height?dungeon.height*24:336}}</span></div><div class="canvas"><div class="route-frame"><img :src="frame" alt="真实模拟世界渲染画面"><svg v-if="researchMode&&dungeon.width" :viewBox="'0 0 '+dungeon.width+' '+dungeon.height"><polyline v-if="dungeon.route?.length" :points="dungeon.route.map((p:number[])=>[p[0]+.5,p[1]+.5].join(',')).join(' ')"/><circle v-if="dungeon.player" :cx="dungeon.player[0]+.5" :cy="dungeon.player[1]+.5" r=".35"/></svg></div></div><div class="legend"><span class="blue">● 角色</span><span class="red">● 怪物</span><span class="yellow">● 掉落</span><span class="purple">● 出口</span><span>● 入口</span><span>■ 障碍</span></div></div>
      <div class="panel controls"><div class="panel-title"><h2>执行控制</h2><span class="state">{{ statusKnown?(status.paused?'PAUSED':status.state):'待获取' }}</span></div><label class="scene-select">执行场景<select v-model="scene" :disabled="status.running||busy" @change="fault=''"><option v-for="s in scenes" :key="s.id" :value="s.id">{{s.name}}</option></select></label><div class="settings"><label>副本轮数<input v-model.number="rounds" type="number" min="1" max="100" :disabled="status.running"></label><label>随机种子<input v-model.number="seed" type="number" min="0" :disabled="status.running"></label></div><label class="fault">故障注入<select v-model="fault" :disabled="status.running"><option value="">无故障</option><option value="stuck">一次移动阻塞 · 恢复</option><option value="crash">Worker 异常 · 明确失败</option><template v-if="researchMode"><option value="target_disappeared">目标消失 · 重新识别</option><option value="path_blocked">动态障碍 · 重新寻路</option><option value="character_dead">角色死亡 · 失败</option><option value="vision_failed">视觉失败一次 · 重采样</option><option value="vision_persistent">持续视觉失败 · 有限停止</option><option value="stage_timeout">首领阶段超时 · 失败</option><option value="loot_failed">拾取失败一次 · 重试</option><option value="loot_persistent">持续拾取失败 · 有限停止</option><option value="bag_full">背包容量不足 · 模拟转存</option><option value="scene_load_failed">场景加载失败</option><option value="worker_interrupted">Worker 异常中断</option></template></select></label><div class="buttons"><button class="primary" @click="control('start')" :disabled="!canControl||status.running">▶ 启动 Agent</button><button @click="control(status.paused?'resume':'pause')" :disabled="!canControl||!status.running">{{status.paused?'继续':'暂停'}}</button><button @click="control('stop')" :disabled="!canControl||!status.running">停止</button><button class="danger" @click="control('emergency-stop')" :disabled="!canControl||!status.running">紧急停止</button></div><div v-if="syncError||dataError||error||status.error" class="error" role="alert"><p v-if="syncError">{{syncError}}</p><p v-if="dataError">{{dataError}}</p><p v-if="error||status.error">{{error||status.error}}</p><button @click="retry" :disabled="busy">重新同步 / 重试连接</button></div><p class="connection-time">最后成功连接：{{lastConnected??'待获取'}}<span v-if="!apiConnected&&statusKnown"> · 以下为最后已知状态，当前 Worker 状态待确认</span></p><dl><dt>运行时间</dt><dd>{{metric(status.elapsedSeconds,1)}} 秒</dd><dt>首领阶段</dt><dd>{{metric(dungeon.stage)}} / 4 · {{dungeon.stageName??'待获取'}}</dd><dt>BOSS / 怪物存活</dt><dd>{{metric(dungeon.bossRemaining)}}</dd><dt>角色生命 / 攻击</dt><dd>{{metric(dungeon.hp)}} / {{metric(dungeon.attack)}}</dd><dt>本轮背包</dt><dd>{{metric(inventory.gold)}} 金币 · {{metric(inventory.shards)}} 碎片 · {{metric(inventory.materials)}} 材料</dd><dt v-if="researchMode">模拟背包空位 / 容量</dt><dd v-if="researchMode">{{bagCurrent?bagFree(dungeon.bag_capacity,dungeon.bag_used):'待获取'}} / {{bagCurrent?metric(dungeon.bag_capacity):'待获取'}}</dd><dt v-if="researchMode">模拟补给成本（份）</dt><dd v-if="researchMode">{{bagCurrent?metric(dungeon.consumable_cost):'待获取'}}</dd><dt>已执行动作</dt><dd>{{metric(dungeon.actions)}}</dd></dl><div class="progress"><span :style="{width:(progress??0)+'%'}"></span></div><small>阶段进度 {{calculated(progress,0)}}% · 本次完成 {{metric(status.completedSession)}} / {{metric(status.rounds)}}</small><p class="run-id">{{status.runId||'等待创建运行实例'}}</p></div></section>
      <section v-if="researchMode" class="panel history research-report"><div class="panel-title"><h2>九星模拟收益报表</h2><span>SIMULATOR · 不换算人民币 · 成本单位：模拟补给份</span></div><div class="metrics"><article><label>完成 / 失败 / 停止</label><strong>{{metric(researchEarnings.completed)}} / {{metric(researchEarnings.failed)}} / {{metric(researchEarnings.stopped)}}</strong></article><article><label>实际成功率（完成与失败样本）</label><strong>{{researchEarnings.sample_count>0?calculated(ratio(researchEarnings.completed,researchEarnings.sample_count,100))+'%':'暂无有效数据'}}</strong><small>{{metric(researchEarnings.sample_count)}} 次；主动停止另列</small></article><article><label>成功结算模拟金币</label><strong>{{metric(researchEarnings.simulated_gold)}}</strong></article><article><label>模拟碎片 / 材料</label><strong>{{metric(researchEarnings.simulated_fragments)}} / {{metric(researchEarnings.simulated_materials)}}</strong></article></div><div class="table-wrap"><table><thead><tr><th>运行</th><th>状态</th><th>模拟金币 / 碎片 / 材料</th><th>补给份</th><th>实际时长</th></tr></thead><tbody><tr v-for="r in researchEarnings.items" :key="r.run_id"><td>{{r.run_id.slice(0,8)}}</td><td>{{r.status}}</td><td>{{metric(r.simulated_gold)}} / {{metric(r.simulated_fragments)}} / {{metric(r.simulated_materials)}}</td><td>{{metric(r.consumable_cost)}}</td><td>{{metric(r.duration,2)}}秒</td></tr></tbody></table></div></section>
      <section v-if="researchMode" class="panel history"><div class="panel-title"><h2>Agent 自主决策记录</h2><span>像素证据 → 目标 → A* → 动作 → 结果</span></div><div class="decision-list"><article v-for="d in decisions" :key="d.id"><b>阶段 {{d.stage_id}} · {{d.action_type}} · {{d.error_code??'已验证'}}</b><p>{{d.decision_reason}}</p><small>规划耗时 {{metric(d.decision_latency,3)}}毫秒 · {{d.observation_id}}</small><details @toggle="expandDecision($event,d.id)"><summary>原始图像与完整结构化依据</summary><img v-if="expandedDecisions.has(d.id)" :src="'/api/research/observations/'+d.observation_id+'/frame'" alt="本次决策实际采样图像"><pre v-if="expandedDecisions.has(d.id)">{{JSON.stringify(d,null,2)}}</pre></details></article><p v-if="!decisions.length" class="empty">尚无实际研究决策，选择九星研究副本并启动。</p></div></section>
      <section class="panel log-panel"><div class="panel-title"><h2>实时事件日志 <small>{{logs.length}}</small></h2><div class="filters"><select v-model="level"><option value="">全部级别</option><option>INFO</option><option>WARN</option><option>ERROR</option></select><input v-model="search" placeholder="搜索事件或详情"><label><input v-model="scroll" type="checkbox"> 自动滚动</label></div></div><div class="logs" ref="logBox"><div v-for="l in filtered" :key="l.id" class="log-line"><time>{{new Date(l.timestamp).toLocaleTimeString()}}</time><b :class="l.level">{{l.level}}</b><span class="log-stage">{{l.stage}}</span><span>{{l.message}}</span><details @toggle="expandLog($event,l.id)"><summary>#{{l.id}} / {{l.sequence}}</summary><pre v-if="expandedLogs.has(l.id)">{{JSON.stringify(l.details,null,2)}}</pre><small>{{l.runId}}</small></details></div><p v-if="!filtered.length" class="empty">等待真实 Agent 事件</p></div></section>
      <section id="history" class="panel history"><div class="panel-title"><h2>历史任务与事件回放</h2><span>SQLite 持久化</span></div><div class="table-wrap"><table><thead><tr><th>任务 ID</th><th>开始时间</th><th>状态</th><th>耗时</th><th>SIMULATED 金币</th><th></th></tr></thead><tbody><tr v-for="run in shownRuns" :key="run.id"><td>{{run.id.slice(0,8)}}</td><td>{{new Date(run.started).toLocaleString()}}</td><td>{{run.status}}</td><td>{{metric(run.duration,1)}} 秒</td><td>{{metric(run.rewards?.gold)}}</td><td><button @click="inspect(run.id)">查看 / 回放</button></td></tr></tbody></table><p v-if="!shownRuns.length" class="empty">尚无执行任务</p></div></section>
      <section v-if="selected" class="panel replay"><div class="panel-title"><h2>事件回放 · {{selected.id.slice(0,8)}}</h2><button @click="selected=null">关闭</button></div><p v-if="selected.error" class="error">{{selected.error}}</p><p>回放数据库记录的事件序列（不是历史画面录像）</p><button @click="toggleReplay">{{playing?'暂停回放':'播放事件'}}</button><input v-model.number="replay" type="range" min="0" :max="Math.max(0,selected.events.length-1)"><pre>{{JSON.stringify(selected.events[replay],null,2)}}</pre><div v-if="replayDecisions.length" class="decision-replay"><h3>九星决策图像回放</h3><input v-model.number="decisionIndex" type="range" min="0" :max="replayDecisions.length-1"><img :src="'/api/research/observations/'+replayDecisions[decisionIndex].observation_id+'/frame'" alt="数据库保存的历史决策图像"><pre>{{JSON.stringify(replayDecisions[decisionIndex],null,2)}}</pre></div><a :href="'/api/logs/export?runId='+selected.id">导出本任务 JSONL</a></section>
      <footer>仅在自建模拟器执行 · 127.0.0.1 本地服务 · 官网历史知识与观察证据独立保存</footer>
    </main>
  </div>
</template>
