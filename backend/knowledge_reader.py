"""给 Agent 扩展或人工分析使用的只读查询客户端；不生成 Decision 或动作。"""
import httpx


class KnowledgeReader:
    def __init__(self,base_url='http://127.0.0.1:8000'):
        self.base_url=base_url.rstrip('/')

    def get(self,path):
        if path not in ('dungeons','dungeons/alama','dungeons/alama/stages','dungeons/alama/bosses',
                        'dungeons/alama/rewards','sources','conflicts'):
            raise ValueError('只允许预定义的只读知识查询')
        response=httpx.get(self.base_url+'/api/knowledge/'+path,timeout=10)
        response.raise_for_status()
        return response.json()
