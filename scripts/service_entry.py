import asyncio,os,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import uvicorn

async def main():
    stop=Path(os.environ['MOYU_SERVICE_DIR'])/'stop.json'
    log_config={'version':1,'disable_existing_loggers':False,'formatters':{'default':{'format':'%(asctime)s %(levelname)s %(name)s %(message)s'}},'handlers':{'file':{'class':'logging.handlers.RotatingFileHandler','filename':str(stop.parent/'service.log'),'maxBytes':5242880,'backupCount':5,'encoding':'utf-8','formatter':'default'}},'root':{'handlers':['file'],'level':'INFO'},'loggers':{'uvicorn':{'handlers':['file'],'level':'INFO','propagate':False},'uvicorn.error':{'level':'INFO'},'uvicorn.access':{'level':'INFO'}}}
    server=uvicorn.Server(uvicorn.Config('backend.main:app',host='127.0.0.1',port=int(os.environ['MOYU_PORT']),timeout_graceful_shutdown=5,limit_concurrency=128,log_config=log_config))
    async def watch_stop():
        while True:
            if stop.exists():server.should_exit=True;return
            await asyncio.sleep(.2)
    watcher=asyncio.create_task(watch_stop())
    try:await server.serve()
    finally:
        watcher.cancel()
        try:await watcher
        except asyncio.CancelledError:pass

if __name__=='__main__':asyncio.run(main())
