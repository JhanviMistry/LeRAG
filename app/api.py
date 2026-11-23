"""The HTTP API.

Every endpoint except /health and /auth/login needs a valid session token. Endpoints add NO permission
logic for reading: they pass the verified user id to the code underneath, and the database decides what that
user may see. Run it with:   uvicorn app.api:app --reload      then open http://127.0.0.1:8000/docs
"""
import math
from typing import Literal
from uuid import UUID

import psycopg
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from pydantic import BaseModel, Field, field_validator

from app.answering import answer_question
from app.auth import SESSION_TTL_SECONDS, authenticate, issue_session_token, verify_session_token
from app.db import user_session
from app.ingest import ingest_document
from app.ratelimit import RateLimiter
from app.retrieval import search as run_search

MAX_BODY_BYTES = 2_000_000
MAX_QUESTION_CHARS = 1000
MAX_PAGES = 500
MAX_PAGE_CHARS = 20_000
MAX_TOTAL_CHARS = 400_000

LOGIN_EMAIL_LIMITER = RateLimiter(max_events=5, window_seconds=900)    # 5 failed logins per email per 15 min
LOGIN_IP_LIMITER = RateLimiter(max_events=20, window_seconds=900)      # 20 failed logins per address per 15 min
QUERY_LIMITER = RateLimiter(max_events=30, window_seconds=60)          # 30 searches/questions/uploads per user per minute

app = FastAPI(
    title="Secure Legal RAG (demo)",
    description="Matter-isolated document search and cited answers. All data is synthetic. "
                "Click Authorize, log in with a demo user, then try the endpoints.",
    version="0.1.0",
)


# ---------- request size ----------
@app.middleware("http")
async def limit_body_size(request: Request, call_next):
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > MAX_BODY_BYTES:
        return JSONResponse({"detail": "Request body too large"}, status_code=413)
    return await call_next(request)


# ---------- models ----------
class TokenOut(BaseModel):
    access_token: str
    token_type: str
    expires_in: int


class MatterOut(BaseModel):
    id: str
    name: str
    client_ref: str


class SearchRequest(BaseModel):
    question: str = Field(min_length=1, max_length=MAX_QUESTION_CHARS)
    matter_id: UUID | None = None
    top_k: int = Field(default=5, ge=1, le=20)


class HitOut(BaseModel):
    chunk_id: str
    document_id: str
    citation: str
    page: int
    text: str
    score: float
    similarity: float | None
    vector_rank: int | None
    keyword_rank: int | None


class AskRequest(BaseModel):
    question: str = Field(min_length=1, max_length=MAX_QUESTION_CHARS)
    matter_id: UUID | None = None


class ClaimOut(BaseModel):
    text: str
    quote: str
    citation: str
    page: int


class SourceOut(BaseModel):
    citation: str
    similarity: float | None


class AskResponse(BaseModel):
    status: str
    reason: str
    claims: list[ClaimOut]
    sources: list[SourceOut]


class DocumentUpload(BaseModel):
    matter_id: UUID
    title: str = Field(min_length=1, max_length=300)
    doc_type: str = Field(min_length=1, max_length=50)
    confidentiality: Literal["standard", "privileged"] = "standard"
    pages: list[str] = Field(min_length=1, max_length=MAX_PAGES)

    @field_validator("pages")
    @classmethod
    def check_page_sizes(cls, pages):
        if any(len(p) > MAX_PAGE_CHARS for p in pages):
            raise ValueError(f"each page must be at most {MAX_PAGE_CHARS} characters")
        if sum(len(p) for p in pages) > MAX_TOTAL_CHARS:
            raise ValueError(f"a document may hold at most {MAX_TOTAL_CHARS} characters in total")
        return pages


class DocumentOut(BaseModel):
    document_id: str


# ---------- dependencies ----------
bearer = OAuth2PasswordBearer(tokenUrl="auth/login")


def too_many(wait: float) -> HTTPException:
    return HTTPException(429, "Too many requests. Try again later.",
                         headers={"Retry-After": str(max(1, math.ceil(wait)))})


def current_user(token: str = Depends(bearer)) -> str:
    user_id = verify_session_token(token)
    if user_id is None:
        raise HTTPException(401, "Invalid or expired token", headers={"WWW-Authenticate": "Bearer"})
    return user_id


def limited_user(user_id: str = Depends(current_user)) -> str:
    """A logged-in user who is also within their request allowance."""
    wait = QUERY_LIMITER.acquire(user_id)
    if wait:
        raise too_many(wait)
    return user_id


# ---------- endpoints ----------
@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/auth/login", response_model=TokenOut)
def login(request: Request, form: OAuth2PasswordRequestForm = Depends()):
    email = form.username.strip().lower()[:254]
    ip = request.client.host if request.client else "unknown"
    checks = ((LOGIN_EMAIL_LIMITER, f"email:{email}"), (LOGIN_IP_LIMITER, f"ip:{ip}"))

    for limiter, key in checks:                 # throttled callers are stopped before any password work
        wait = limiter.retry_after(key)
        if wait:
            raise too_many(wait)

    user_id = authenticate(email, form.password)
    if user_id is None:
        for limiter, key in checks:
            limiter.record(key)
        raise HTTPException(401, "Incorrect email or password", headers={"WWW-Authenticate": "Bearer"})

    LOGIN_EMAIL_LIMITER.reset(f"email:{email}")
    return TokenOut(access_token=issue_session_token(user_id), token_type="bearer", expires_in=SESSION_TTL_SECONDS)


@app.get("/matters", response_model=list[MatterOut])
def list_matters(user_id: str = Depends(current_user)):
    with user_session(user_id) as conn:         # row-level security returns only this user's matters
        rows = conn.execute("SELECT id, name, client_ref FROM matters ORDER BY name").fetchall()
    return [MatterOut(id=str(r[0]), name=r[1], client_ref=r[2]) for r in rows]


@app.post("/search", response_model=list[HitOut])
def search_documents(body: SearchRequest, user_id: str = Depends(limited_user)):
    try:
        hits = run_search(user_id, body.question, matter_id=body.matter_id, top_k=body.top_k)
    except ValueError as e:
        raise HTTPException(422, str(e))
    return [HitOut(chunk_id=str(h.chunk_id), document_id=str(h.document_id), citation=h.citation, page=h.page,
                   text=h.text, score=h.score, similarity=h.similarity,
                   vector_rank=h.vector_rank, keyword_rank=h.keyword_rank) for h in hits]


@app.post("/ask", response_model=AskResponse)
def ask(body: AskRequest, user_id: str = Depends(limited_user)):
    try:
        a = answer_question(user_id, body.question, matter_id=body.matter_id)
    except ValueError as e:
        raise HTTPException(422, str(e))
    return AskResponse(
        status=a.status, reason=a.reason,
        claims=[ClaimOut(text=c.text, quote=c.quote, citation=c.citation, page=c.page) for c in a.claims],
        sources=[SourceOut(citation=h.citation, similarity=h.similarity) for h in a.hits])


@app.post("/documents", status_code=201, response_model=DocumentOut)
def upload_document(body: DocumentUpload, user_id: str = Depends(limited_user)):
    matter_id = str(body.matter_id)
    with user_session(user_id) as conn:         # a clean 403 here; the database enforces the same rule again below
        allowed = conn.execute("SELECT app.can_write(%s::uuid)", (matter_id,)).fetchone()[0]
    if not allowed:                             # same answer whether the matter is missing or off-limits
        raise HTTPException(403, "You cannot add documents to this matter")
    try:
        doc_id = ingest_document(matter_id=matter_id, title=body.title.strip(), doc_type=body.doc_type.strip(),
                                 confidentiality=body.confidentiality, pages=body.pages, uploaded_by=user_id)
    except ValueError as e:
        raise HTTPException(422, str(e))
    except psycopg.errors.InsufficientPrivilege:
        raise HTTPException(403, "You cannot add documents to this matter")
    return DocumentOut(document_id=doc_id)