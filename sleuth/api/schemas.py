from pydantic import BaseModel


class UserOut(BaseModel):
    id: str
    email: str | None = None
    name: str | None = None
    theme_preference: str = "storm"


class UpdateMeIn(BaseModel):
    theme_preference: str


class SignupIn(BaseModel):
    email: str
    password: str
    name: str | None = None


class LoginIn(BaseModel):
    email: str
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
    question: str
