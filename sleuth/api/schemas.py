from pydantic import BaseModel


class UserOut(BaseModel):
    id: str
    github_id: int | None = None
    email: str | None = None
    name: str | None = None
    avatar_url: str | None = None
    theme_preference: str = "storm"


class UpdateMeIn(BaseModel):
    theme_preference: str


class EmailIn(BaseModel):
    email: str
