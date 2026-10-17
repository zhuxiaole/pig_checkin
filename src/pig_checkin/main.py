import os
import uvicorn

def run():
    reload = os.environ.get("PIG_RELOAD", "false").lower() in ("true", "1", "yes")
    # CloakBrowser (Playwright) 在 Linux/容器下需使用标准 asyncio 循环，避免 uvloop 管道死锁
    uvicorn.run("pig_checkin.app:app", host="0.0.0.0", port=8000, reload=reload, loop="asyncio")

if __name__ == "__main__":
    run()
