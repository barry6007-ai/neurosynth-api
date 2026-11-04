import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse

app = FastAPI()


@app.middleware("http")
async def add_cors_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["Access-Control-Allow-Origin"] = "*"
    return response


@app.get("/", response_class=HTMLResponse)
async def index(greet: str = "", name: str = "") -> str:
    return f"{greet} {name}"


@app.get("/json")
async def json_endpoint(
    greet: str = "",
    name: str = "",
) -> dict[str, str]:
    return {"greet": greet, "name": name}


if __name__ == "__main__":
    uvicorn.run(
        "2_py_backend_fastapi:app",
        host="127.0.0.1",
        port=5000,
        reload=True,
    )