import os
import uvicorn

def run():
    reload = os.environ.get("PIG_RELOAD", "false").lower() in ("true", "1", "yes")
    uvicorn.run("pig_checkin.app:app", host="0.0.0.0", port=8000, reload=reload)

if __name__ == "__main__":
    run()
