"""公开官网页面的低频、可重复采集。仅提取正文表格，不追踪导航或扩展副本。"""
import hashlib
import json
import re
import time
import unicodedata
from pathlib import Path
from urllib.parse import urlparse
from urllib.robotparser import RobotFileParser
import httpx
from bs4 import BeautifulSoup
from .knowledge import KnowledgeDB, key, DUNGEON_ID, PARSER_VERSION
from .store import now

URLS = (
 'https://my.99.com/content/2015-10-18/20151018225607925.shtml',
 'https://my.99.com/guide/task/alama.shtml',
 'https://my.99.com/content/2010-06-02/20100602040243433.shtml')
USER_AGENT='MoyuKnowledgeLab/1.1 (local research; three public pages only)'


def text(node):
    return re.sub(r'\s+',' ',unicodedata.normalize('NFKC',node.get_text('',strip=True))).strip()


def original_text(node):
    return node.get_text('',strip=True)


def locate(node):
    """保留可重复定位到原始 HTML 元素的 CSS 路径。"""
    parts=[]
    while node and node.name and node.name!='[document]':
        siblings=[n for n in node.parent.children if getattr(n,'name',None)==node.name] if node.parent else [node]
        index=next((i for i,n in enumerate(siblings,1) if n is node),1)
        parts.append(f'{node.name}:nth-of-type({index})')
        node=node.parent
    return ' > '.join(reversed(parts))


def parse_page(raw,url):
    charset=re.search(rb'charset\s*=\s*["\']?([a-zA-Z0-9_-]+)',raw[:5000],re.I)
    encoding=charset.group(1).decode().lower() if charset else 'utf-8'
    if encoding in ('gb2312','gbk'): encoding='gb18030'
    soup=BeautifulSoup(raw.decode(encoding,errors='strict'),'html.parser')
    table=next((t for t in soup.find_all('table') if '副本名称' in text(t) and '阿拉玛的哭泣' in text(t)),None)
    if table is None: raise ValueError('未找到阿拉玛副本资料正文表格，拒绝提取其他区域')
    # 限定正文所在容器；发布时间只采信页面明确的发布标签，不把 URL 日期冒充发布时间。
    published_node=next((n for n in soup.find_all(['p','div']) if '发布时间' in text(n) and re.search(r'\d{4}-\d{2}-\d{2}',text(n)) and len(text(n))<120),None)
    published=re.search(r'\d{4}-\d{2}-\d{2}',text(published_node)).group() if published_node else None
    cells=table.find_all(['td','th'])
    facts=[]
    def add(field,value,raw_value,node):
        original=original_text(node)
        facts.append({'field':field,'value':value,'raw_value':raw_value if raw_value in original else original,'excerpt':original,'locator':locate(node)})
    def field(field,pattern,transform=lambda m:m.group(1)):
        for cell in cells:
            match=re.search(pattern,text(cell),re.I)
            if match:
                add(field,transform(match),match.group(0),cell)
                return
    field('name',r'副本名称\s*[:：]\s*([^ ]+)',lambda m:m.group(1).strip())
    field('minimum_level',r'(?:等级\s*[≥>=]+\s*|准入等级\s*[:：]\s*)(\d+)',lambda m:int(m.group(1)))
    field('minimum_battle_power',r'战斗力\s*[≥>=]+\s*(\d+)',lambda m:int(m.group(1)))
    field('pet_requirement',r'(完成三宠任务|驾驭三宠)')
    field('open_channels',r'副本开放情况\s*[:：]\s*([^ ]+)')
    field('entry_location',r'入口\s*[:：]\s*([^ (（]+)')
    field('entry_coordinates',r'雷鸣大陆\s*[:：]\s*(\d+)\s*[,，]\s*(\d+)',lambda m:[int(m.group(1)),int(m.group(2))])
    field('entry_map',r'(雷鸣大陆)')
    field('open_hours',r'开启时间\s*[:：]\s*(\d{1,2}:\d{2}\s*-\s*\d{1,2}:\d{2})')
    field('team_limit',r'限制人数\s*[:：]\s*(\d+)人队',lambda m:int(m.group(1)))
    field('boss_total',r'(?:BOSS|首领)数量\s*[:：]\s*(\d+)',lambda m:int(m.group(1)))
    field('final_boss',r'最终(?:BOSS|首领)\s*[:：]\s*([^ ]+)')
    stages=[]
    header=next((row for row in table.find_all('tr') if re.search(r'一号(?:BOSS|首领)',text(row),re.I)),None)
    if header is None: raise ValueError('正文缺少阶段 BOSS 行')
    heads=header.find_all(['td','th'],recursive=False)
    if len(heads)!=4: raise ValueError('阶段表结构改变，需人工复核提取器')
    rows=table.find_all('tr')
    index=next(i for i,row in enumerate(rows) if row is header)
    descriptions=rows[index+1].find_all('td',recursive=False)
    drops=rows[index+2].find_all('td',recursive=False)
    if len(descriptions)!=4 or len(drops)!=4: raise ValueError('BOSS 档案或掉落列不匹配，拒绝猜测关联')
    for ordinal,(head,description,drop) in enumerate(zip(heads,descriptions,drops),1):
        title=re.search(r'[一二三四]号(?:BOSS|首领)\s*(.+)',text(head),re.I)
        if not title: raise ValueError('无法识别 BOSS 表头')
        name=title.group(1).strip()
        reward_match=re.search(r'(?:掉落物品|物品掉落)\s*[:：]\s*(.+)',text(drop))
        if not reward_match: raise ValueError('无法识别掉落列表')
        # span/a 链接文字连结，原物品名字与后缀完整保留；绝不推断数量和掉落概率。
        rewards=[v.strip().rstrip('。') for v in re.split('[、，]',reward_match.group(1)) if v.strip().rstrip('。')]
        count=4 if re.search('四个一号(?:BOSS|首领)',text(description),re.I) else None
        stages.append({'ordinal':ordinal,'name':name,'boss_count':count,'head':head,'description':description,'drop':drop,'rewards':rewards})
        add(f'stage_{ordinal}_rewards',rewards,reward_match.group(1),drop)
    add('boss_stage_order',[s['name'] for s in stages],text(header),header)
    images=[image.get('src') for image in table.find_all('img') if image.get('src')]
    if images: add('official_illustration_urls',images,','.join(images),table)
    mechanics=[]
    for cell in cells:
        content=text(cell)
        match=re.search(r'拥有物品[“"](.+?)[”"]可无视时间限制直接进入卡利亚堡',content)
        if match:
            mechanics.append({'name':'入口时间限制豁免','description':match.group(0),'node':cell})
            add('time_bypass_item',match.group(1),match.group(0),cell)
            acquisition=re.search(r'获得方式\s*[:：]\s*([^）)]+)',content)
            if acquisition: add('time_bypass_acquisition',acquisition.group(1),acquisition.group(0),cell)
            break
    required=['name','minimum_level','minimum_battle_power','pet_requirement','open_channels','entry_location','entry_coordinates',
              'entry_map','open_hours','team_limit','boss_total','final_boss','time_bypass_item','time_bypass_acquisition',
              'game_build_number','launch_release_date','map_walkability','map_dimensions','trap_mechanics','boss_combat_mechanics',
              'monster_attributes','consumables','daily_entry_limit','reward_probabilities']
    present={f['field'] for f in facts}
    for field_name in required:
        if field_name not in present:
            facts.append({'field':field_name,'value':None,'raw_value':None,'excerpt':'','locator':locate(table)})
    return {'soup':soup,'table':table,'encoding':encoding,'published_at':published,'facts':facts,'stages':stages,
            'mechanics':mechanics,'article_text':text(table),'article_locator':locate(table)}


class Collector:
    def __init__(self,repository,snapshot_dir,interval=1.5,retries=3,client=None):
        self.repo=repository
        self.snapshot_dir=Path(snapshot_dir)
        self.snapshot_dir.mkdir(parents=True,exist_ok=True)
        self.interval=max(1.0,interval)
        self.retries=max(1,min(retries,5))
        self.client=client or httpx.Client(timeout=25,follow_redirects=False,headers={'User-Agent':USER_AGENT})
        self.last_request=0
        self.robots=None
        self.robots_hash=None

    def request(self,url):
        remaining=self.interval-(time.monotonic()-self.last_request)
        if remaining>0: time.sleep(remaining)
        self.last_request=time.monotonic()
        return self.client.get(url)

    def robots_allowed(self,url):
        if self.robots is None:
            response=self.request('https://my.99.com/robots.txt')
            self.robots_hash=hashlib.sha256(response.content).hexdigest()
            if response.status_code==404: rules=['User-agent: *','Disallow:']
            elif response.status_code==200: rules=response.text.splitlines()
            else: raise RuntimeError(f'robots.txt 不可验证，HTTP {response.status_code}，停止抓取')
            self.robots=RobotFileParser()
            self.robots.parse(rules)
            (self.snapshot_dir/f'robots-{self.robots_hash}.txt').write_bytes(response.content)
            delay=self.robots.crawl_delay(USER_AGENT) or self.robots.crawl_delay('*')
            rate=self.robots.request_rate(USER_AGENT) or self.robots.request_rate('*')
            self.interval=max(self.interval,delay or 0,rate.seconds/rate.requests if rate else 0)
        return self.robots.can_fetch(USER_AGENT,url)

    def collect(self,url):
        if url not in URLS: raise ValueError('仅允许首批明确指定的三个官网页面')
        source_id=key(url)
        hint=re.search(r'/content/(\d{4}-\d{2}-\d{2})/',url)
        with self.repo.lock,self.repo.db:
            self.repo.db.execute('''INSERT OR IGNORE INTO data_source(id,url,source_type,url_date_hint) VALUES(?,?,?,?)''',
                                 (source_id,url,'OFFICIAL_WEB',hint.group(1) if hint else None))
        for attempt in range(self.retries):
            response=None
            try:
                if not self.robots_allowed(url): raise PermissionError('robots.txt 禁止此页面，未发送页面请求')
                response=self.request(url)
                response.raise_for_status()
                if 'text/html' not in response.headers.get('content-type',''):
                    raise ValueError('响应不是 HTML 页面')
                raw=response.content
                digest=hashlib.sha256(raw).hexdigest()
                raw_path=self.snapshot_dir/f'{source_id}-{digest}.html'
                raw_path.write_bytes(raw)
                parsed=parse_page(raw,url)
                snapshot_id=key(source_id,digest)
                timestamp=now()
                existing=self.repo.rows('SELECT id,extractor_version FROM source_snapshot WHERE source_id=? AND sha256=?',(source_id,digest))
                with self.repo.lock,self.repo.db:
                    self.repo.db.execute('''UPDATE data_source SET published_at=?,publication_basis=?,last_fetched_at=?,last_status=?,last_error=NULL,
                        fetch_count=fetch_count+1,robots_url=?,robots_sha256=? WHERE id=?''',
                        (parsed['published_at'],'PAGE_EXPLICIT' if parsed['published_at'] else 'UNKNOWN',timestamp,'SUCCESS',
                         'https://my.99.com/robots.txt',self.robots_hash,source_id))
                    if existing:
                        self.repo.db.execute('UPDATE source_snapshot SET last_seen_at=? WHERE id=?',(timestamp,snapshot_id))
                        status='UNCHANGED'
                        if existing[0]['extractor_version']!=PARSER_VERSION:
                            # 同一 HTML 升级提取器时，旧结果先归档，原始快照与事实 ID 保持稳定。
                            self.repo.db.execute('INSERT OR IGNORE INTO extraction_revision VALUES(?,?,?,?,?,?)',
                                (key(snapshot_id,existing[0]['extractor_version']),snapshot_id,timestamp,existing[0]['extractor_version'],
                                 json.dumps(self.repo.facts(snapshot_id=snapshot_id),ensure_ascii=False),'OFFICIAL_WEB'))
                            version_id=self.repo.db.execute('SELECT version_id FROM source_snapshot WHERE id=?',(snapshot_id,)).fetchone()[0]
                            for table in ('fact_evidence','dungeon_reward','dungeon_boss','dungeon_stage','dungeon_mechanic'):
                                self.repo.db.execute('DELETE FROM '+table+' WHERE snapshot_id=?',(snapshot_id,))
                            self.ingest(parsed,snapshot_id,version_id,timestamp)
                            self.repo.db.execute('UPDATE source_snapshot SET extractor_version=? WHERE id=?',(PARSER_VERSION,snapshot_id))
                            status='REEXTRACTED'
                    else:
                        channel=next((f['value'] for f in parsed['facts'] if f['field']=='open_channels'),None)
                        version_id=key(snapshot_id,'version')
                        label=f"官网页面快照 · {parsed['published_at'] or '发布时间 UNKNOWN'} · 非游戏构建版本"
                        self.repo.db.execute('INSERT INTO game_version(id,label,channel,source_type) VALUES(?,?,?,?)',
                                             (version_id,label,channel,'OFFICIAL_WEB'))
                        self.repo.db.execute('''INSERT INTO source_snapshot VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                            (snapshot_id,source_id,digest,timestamp,timestamp,response.status_code,parsed['encoding'],raw,str(raw_path.resolve()),
                             parsed['article_text'],parsed['article_locator'],parsed['published_at'],version_id,PARSER_VERSION,'OFFICIAL_WEB'))
                        self.ingest(parsed,snapshot_id,version_id,timestamp)
                        status='CREATED'
                    self.repo.db.execute('INSERT INTO collection_attempt(source_id,attempted_at,status,http_status,snapshot_id) VALUES(?,?,?,?,?)',
                                         (source_id,timestamp,status,response.status_code,snapshot_id))
                self.detect_conflicts()
                return {'url':url,'status':status,'http_status':response.status_code,'sha256':digest,'snapshot_id':snapshot_id}
            except Exception as exc:
                timestamp=now()
                with self.repo.lock,self.repo.db:
                    self.repo.db.execute('UPDATE data_source SET last_fetched_at=?,last_status=?,last_error=?,fetch_count=fetch_count+1 WHERE id=?',
                                         (timestamp,'FAILED',str(exc),source_id))
                    self.repo.db.execute('INSERT INTO collection_attempt(source_id,attempted_at,status,http_status,error) VALUES(?,?,?,?,?)',
                                         (source_id,timestamp,'FAILED',response.status_code if response else None,str(exc)))
                if isinstance(exc,PermissionError) or (response is not None and response.status_code in (401,403,404)): break
                if attempt+1<self.retries: time.sleep(min(2**attempt,8))
        return {'url':url,'status':'FAILED','error':self.repo.rows('SELECT last_error FROM data_source WHERE id=?',(source_id,))[0]['last_error']}

    def ingest(self,parsed,snapshot_id,version_id,timestamp):
        db=self.repo.db
        db.execute('INSERT OR IGNORE INTO dungeon_definition VALUES(?,?,?)',(DUNGEON_ID,'阿拉玛的哭泣','OFFICIAL_WEB'))
        def fact(entity,entity_id,field,value,raw_value,node=None,excerpt=None,locator=None):
            original=original_text(node) if node is not None else (excerpt or '')
            if value is None: original=''
            if raw_value is not None and raw_value not in original: raw_value=original
            db.execute('''INSERT OR IGNORE INTO fact_evidence VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                (key(snapshot_id,entity,entity_id,field),DUNGEON_ID,entity,entity_id,field,'OFFICIAL_WEB',snapshot_id,version_id,
                 'UNKNOWN' if value is None else 'OFFICIAL_PAGE_EXTRACTED',raw_value,json.dumps(value,ensure_ascii=False),
                 original,locate(node) if node is not None else locator,
                 parsed['published_at'],timestamp,PARSER_VERSION))
        for f in parsed['facts']: fact('dungeon',DUNGEON_ID,f['field'],f['value'],f['raw_value'],excerpt=f['excerpt'],locator=f['locator'])
        for stage in parsed['stages']:
            ordinal=stage['ordinal'];sid=key(snapshot_id,'stage',ordinal);bid=key(snapshot_id,'boss',ordinal)
            db.execute('INSERT INTO dungeon_stage VALUES(?,?,?,?,?,?,?,?,?)',(sid,DUNGEON_ID,snapshot_id,version_id,ordinal,stage['name'],stage['boss_count'],'OFFICIAL_WEB',0))
            fact('stage',sid,'ordinal',ordinal,text(stage['head']),stage['head'])
            fact('stage',sid,'name',stage['name'],text(stage['head']),stage['head'])
            fact('stage',sid,'boss_count',stage['boss_count'],text(stage['description']) if stage['boss_count'] else None,stage['description'])
            db.execute('INSERT INTO dungeon_boss VALUES(?,?,?,?,?,?,?,?,?,?)',(bid,DUNGEON_ID,sid,snapshot_id,version_id,stage['name'],None,None,None,'OFFICIAL_WEB'))
            fact('boss',bid,'name',stage['name'],text(stage['head']),stage['head'])
            fact('boss',bid,'description',text(stage['description']),text(stage['description']),stage['description'])
            for attribute in ('hp','attack','defense','skills'):
                fact('boss',bid,attribute,None,None,stage['description'],excerpt='')
            for reward in stage['rewards']:
                iid=key('item',reward);rid=key(snapshot_id,bid,reward)
                db.execute('INSERT OR IGNORE INTO game_item VALUES(?,?,?)',(iid,reward,'OFFICIAL_WEB'))
                db.execute('INSERT INTO dungeon_reward VALUES(?,?,?,?,?,?,?,?,?)',(rid,DUNGEON_ID,bid,iid,snapshot_id,version_id,None,None,'OFFICIAL_WEB'))
                fact('reward',rid,'item_name',reward,reward,stage['drop'])
                fact('reward',rid,'quantity',None,None,stage['drop'])
                fact('reward',rid,'drop_rate',None,None,stage['drop'])
                fact('item',iid,'name',reward,reward,stage['drop'])
        for mechanic in parsed['mechanics']:
            mid=key(snapshot_id,mechanic['name'])
            db.execute('INSERT INTO dungeon_mechanic VALUES(?,?,?,?,?,?,?,?)',(mid,DUNGEON_ID,snapshot_id,version_id,mechanic['name'],mechanic['description'],'OFFICIAL_WEB',0))
            fact('mechanic',mid,'description',mechanic['description'],mechanic['description'],mechanic['node'])

    def detect_conflicts(self):
        facts=self.repo.facts('dungeon',DUNGEON_ID)
        groups={}
        for f in facts:
            if f['normalized_value'] is not None: groups.setdefault(f['field'],[]).append(f)
        with self.repo.lock,self.repo.db:
            for field,values in groups.items():
                if len({json.dumps(v['normalized_value'],ensure_ascii=False,sort_keys=True) for v in values})<2: continue
                self.repo.db.execute('''INSERT INTO data_conflict VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET evidence_ids=excluded.evidence_ids''',
                  (key(DUNGEON_ID,field),DUNGEON_ID,'dungeon',DUNGEON_ID,field,json.dumps([v['id'] for v in values]),'OPEN',
                   '不同官网来源或页面快照的值存在差异；可能为历史版本差异，尚未人工确认，不自动覆盖。','OFFICIAL_WEB'))

    def collect_all(self):
        return {'collected_at':now(),'results':[self.collect(url) for url in URLS],'stats':self.repo.stats()}
