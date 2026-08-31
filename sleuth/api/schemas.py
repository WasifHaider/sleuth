from typing import Literal
from urllib.parse import urlparse

from pydantic import BaseModel, EmailStr, Field, field_validator

# Only the 2 themes actually implemented (theme.css's [data-theme="..."]
# blocks) — storm (dark) and ivory (light). Previously allowed midnight/
# edition/leaf too, but those were dropped by product decision (2026-08-29)
# in favor of just these two. Without this constraint UpdateMeIn accepted
# ANY string, which then got persisted and echoed straight back into the
# signed session cookie's claims on every subsequent request.
Theme = Literal["storm", "ivory"]


class UserOut(BaseModel):
    id: str
    email: str | None = None
    name: str | None = None
    theme_preference: str = "storm"


class UpdateMeIn(BaseModel):
    theme_preference: Theme


class SignupIn(BaseModel):
    email: EmailStr
    password: str
    name: str | None = None


class LoginIn(BaseModel):
    email: EmailStr
    password: str


class RepoOut(BaseModel):
    id: str
    github_url: str
    status: str
    error_message: str | None = None
    embedding_model: str | None = None
    embedding_dim: int | None = None


class AddRepoIn(BaseModel):
    github_url: str

    @field_validator("github_url")
    @classmethod
    def _must_be_a_github_https_url(cls, value: str) -> str:
        # Before this, a malformed github_url (typo'd scheme, empty string,
        # a non-GitHub host, no host at all) sailed straight into a created
        # repo row and a kicked-off background ingest task — the only
        # place it would ever surface was clone_repo's subprocess failing
        # minutes later, as an ingestion-time 'failed' status instead of an
        # immediate, clear 400 at the point the user actually made the typo.
        parsed = urlparse(value)
        if parsed.scheme != "https" or parsed.netloc.lower() != "github.com" or not parsed.path.strip("/"):
            raise ValueError("github_url must be an https://github.com/<owner>/<repo> URL")
        return value


class CreateChatIn(BaseModel):
    repo_id: str


class ChatOut(BaseModel):
    id: str
    title: str
    created_at: str
    message_count: int = 0


class MessageOut(BaseModel):
    id: str
    role: str
    content: str
    sources: list[dict] | None = None
    created_at: str


class SendMessageIn(BaseModel):
    chat_id: str
    # Unbounded before this — a client could post an arbitrarily large
    # question straight into the DB and the LLM prompt context. 8000 chars
    # is generous for an actual question (way beyond anything reasonable to
    # type) while still bounding worst-case payload/token cost per request.
    question: str = Field(max_length=8000)
