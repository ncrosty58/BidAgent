import asyncio
import time
import httpx
from src.main import app as main_app
import uvicorn
import threading

def run_slow_server():
    uvicorn.run("slow_server:app", host="127.0.0.1", port=9999, log_level="critical")

def run_main_app():
    uvicorn.run("src.main:app", host="127.0.0.1", port=8000, log_level="critical")

async def run_benchmark():
    async with httpx.AsyncClient() as client:
        start_time = time.time()

        data = {
            "requested_services": "none",
            "image_urls": "http://127.0.0.1:9999/image/1,http://127.0.0.1:9999/image/2,http://127.0.0.1:9999/image/3"
        }

        response = await client.post("http://127.0.0.1:8000/api/v1/estimate", data=data)

        end_time = time.time()

        print(f"Status: {response.json().get('status')}")
        print(f"Rejection: {response.json().get('rejection')}")
        print(f"Time taken: {end_time - start_time:.2f} seconds")

if __name__ == "__main__":
    t = threading.Thread(target=run_slow_server, daemon=True)
    t.start()

    t2 = threading.Thread(target=run_main_app, daemon=True)
    t2.start()

    time.sleep(1) # wait for server to start
    asyncio.run(run_benchmark())
