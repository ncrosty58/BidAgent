from fastapi import FastAPI, Response
import asyncio

app = FastAPI()

@app.get("/image/{id}")
async def get_image(id: int):
    await asyncio.sleep(1)
    return Response(content=b"fake image data", media_type="image/jpeg")
