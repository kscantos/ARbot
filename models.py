from pydantic import BaseModel

class ChatRequest(BaseModel):
    text: str
    top_k: int = 5

class ChatResponse(BaseModel):
    query: str
    results: list