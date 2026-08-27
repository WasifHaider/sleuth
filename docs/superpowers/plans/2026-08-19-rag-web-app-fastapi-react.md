# RAG Web App (FastAPI + React) Implementation Plan — Plan 2

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.
>
> **Project-specific override:** this repo's `CLAUDE.md` "Execution mode" section takes precedence over the sub-skill's default flow — work one full task at a time (not step-by-step with per-step confirmation), explain the concept after each task, log it to `docs/progress.html`, and wait for the user's "okay" before starting the next task. Git commits are done by the user, not Claude — the "Commit" step in each task below documents what *would* be committed; do not run it yourself unless asked.

**Goal:** Expose the existing pipeline (ingest/retrieve, done in Plan 1)
through a FastAPI backend and a React (Vite) app implementing the design
built in Claude Design (landing page, GitHub+email login, connect-repo flow,
dashboard, indexing status, chat, repo settings) — log in, add a repo by URL,
watch indexing progress live, chat against a ready repo with streamed
answers and persisted history. Eval stays CLI-only for this plan (see below).

**Design source (revised 2026-08-24):** local Claude Design export
`Sleuth code intelligence landing page.zip`, 9 files: `Sleuth Landing.dc.html`,
`Sleuth Login.dc.html`, `Sleuth Connect Repo.dc.html`, `Sleuth Dashboard.dc.html`,
`Sleuth Indexing Status.dc.html`, `Sleuth Chat.dc.html`, `Sleuth Repo Settings.dc.html`,
plus two shared components reused via `dc-import` across the logged-in screens:
`Sleuth Nav.dc.html` (mobile-responsive variant) and `Sleuth Rail.dc.html`
(the one actually imported by Dashboard/Indexing/Chat/Settings).
This **replaces** the 2026-08-19 design source and its 5-screen scope
(Landing/Repos/Indexing/Chat/Eval, no auth) — superseded, do not build against
the palette/screens described further down in old task bodies without
cross-checking this section first.

**Design doc:** `docs/superpowers/specs/2026-08-13-rag-code-chatbot-design-v2.md`
(`api/main.py`, `web/` sections) — its Non-Goal of "no auth/multi-user" is
now superseded per the 2026-08-24 decision below; still no multi-user data
separation (see Auth section).

**Architecture:** `sleuth/api/` is a FastAPI app calling the same `sleuth/`
modules the CLI already calls (`store.py`, `ingest/pipeline.py`,
`retrieve/answer.py`) — no logic duplicated. Indexing runs as a FastAPI
`BackgroundTasks` job (same process, no queue/worker infra). Chat answers
stream to the browser via Server-Sent Events (SSE), reusing the existing
token-generator from `retrieve/answer.py::stream_answer`. Three existing
pipeline functions (`ingest_repo`, `VoyageEmbedder.embed_batch`,
`stream_answer`) get one small additive change each: an optional callback
parameter (default `None`, fully backward compatible with every existing call
site and test) so the API layer can observe progress/sources without
duplicating any pipeline logic. React talks to the API over plain `fetch` +
`react-router-dom` — no Redux/React Query, no TypeScript, kept simple since
this is the user's first React project.

**Tech stack additions:** FastAPI, uvicorn, `httpx`'s `TestClient`
(`fastapi.testclient`) for backend tests. React 18 + Vite, `react-router-dom`,
plain `fetch` + `ReadableStream` for SSE consumption (`EventSource` can't
send a POST body). Fonts per the new design: `Big Shoulders Display` (headers,
Type A only — see Design System), `Space Grotesk` (body/UI), `JetBrains Mono`
(data/labels/code) — loaded via Google Fonts `<link>` in `index.html`.
Auth additions: `authlib` (or a hand-rolled OAuth2 code-exchange via `httpx`
— **decide in Task 0**, prefer hand-rolled per the project's no-vendor-SDK
philosophy since GitHub's OAuth flow is a handful of REST calls, not a whole
SDK's worth) for the GitHub OAuth code exchange, `itsdangerous` for signed
session cookies, and an SMTP client (stdlib `smtplib`/`email`, no vendor SDK)
against AWS SES's SMTP interface for magic-link email delivery.

## Auth (added 2026-08-24, was previously explicitly out of scope)

> **Superseded 2026-08-24 (later same day):** GitHub OAuth + email magic
> link, as built in Task 0, was replaced with plain **email + password**
> signup/login to avoid the GitHub OAuth App registration and SMTP setup
> for what's still a single-expected-user tool — the user's own call,
> prioritizing shipping speed over the login-method breadth. `bcrypt` for
> password hashing (same "don't hand-roll crypto" exception already made
> for `itsdangerous`). The section below is kept as-written for history;
> read `users` table shape, `Config` fields, and `/auth/*` routes as
> **historical**, not current — current shape: `users(id, email UNIQUE,
> password_hash, name, theme_preference, created_at)`, no `github_id`/
> `avatar_url`, no `smtp_*`/`github_client_*`/`frontend_url` Config fields.
> Routes are `POST /auth/signup`, `POST /auth/login`, `POST /auth/logout`.
> Session cookie mechanism (`itsdangerous`, `require_session`, gate-not-
> multi-tenancy model) is **unchanged** by this.

Two login methods, both shown on `Sleuth Login.dc.html`: **GitHub OAuth**
(primary) and **email magic link** (fallback). GitLab/Bitbucket buttons in
the design are dropped — GitHub only, per user decision.

- **GitHub OAuth App** (not Device Flow, not a pasted PAT): standard
  authorization-code flow. Register a GitHub OAuth App (user does this
  manually in GitHub settings, same pattern as the project's existing
  "user sets up their own accounts/keys" convention for Voyage/Groq).
  Callback exchanges `code` for an access token via a direct POST to
  `https://github.com/login/oauth/access_token` (raw `httpx`, no SDK,
  consistent with the rest of the project's Voyage/NIM/Groq calls) then
  `GET https://api.github.com/user` for the profile (id, login, email,
  avatar).
- **Email magic link**: user submits an email, backend generates a
  signed, time-limited token (`itsdangerous.URLSafeTimedSerializer`,
  ~15 min expiry), emails a login link via SMTP. Dev/local: point at a
  throwaway SMTP catcher (e.g. Mailpit/MailHog) or a real AWS SES SMTP
  endpoint in sandbox mode. Prod (when the user deploys to AWS): AWS SES
  SMTP credentials via `config.py`/env vars, same pattern as
  `VOYAGE_API_KEY`/`GROQ_API_KEY`. New `Config` fields: `smtp_host`,
  `smtp_port`, `smtp_username`, `smtp_password`, `smtp_from_address`.
- **Sessions**: no per-user data separation is needed (single expected
  user), so auth is a gate, not a multi-tenancy boundary — but it's still a
  real login: a signed session cookie (`itsdangerous`, `httponly`,
  `samesite=lax`) issued on successful GitHub callback or magic-link click,
  validated by FastAPI middleware on every request. New `users` table
  (`id, github_id NULLABLE, email NULLABLE UNIQUE, name, avatar_url,
  created_at`) — one row is the expected common case, but the schema doesn't
  hardcode a single-user assumption. No repo/chat rows gain a `user_id` FK
  in this plan (per decision: gatekeeping only, not data separation) — that
  is a clearly-flagged future task if multi-user is ever needed.
- All `/repos`, `/chats`, `/eval`-style routes require a valid session;
  unauthenticated requests get 401. `/auth/*` routes (github redirect,
  github callback, magic-link request, magic-link verify, logout) are the
  only unauthenticated routes besides static assets.
- This becomes **Task 0** (before the old Task 1), since every other route
  now sits behind it.

## Design System (transcribed from the new `.dc.html` files)

Theme is **not** a single hardcoded palette this time — the design ships 5
color themes (Storm/Midnight/Ivory/Leaf/Edition) via a `data-theme` attribute
and CSS custom properties per theme. Per user decision 2026-08-24, the color
theme switcher **is a real shipped feature** (persisted per user, stored on
the `users` row as `theme_preference`, defaulting to `storm`) — unlike the
old design's accent-color-picker, which stayed an authoring-only control.
The **Type A/B font toggle is not shipped** — hardcode Type A
(`Big Shoulders Display` headers / `Space Grotesk` body) only; drop the
`data-type="b"` CSS branch (`Alfa Slab One`/`Sacramento`) entirely from the
ported components.

Reference palette (`storm`, the default) as CSS custom properties for
`web/src/theme.css`:

```css
[data-theme] {
  --bg:#0F372F; --deep:#0A2621; --surface:#143F36; --text:#F2F5F2;
  --muted:rgba(242,245,242,0.58); --faint:rgba(242,245,242,0.30);
  --accent:#ECBC6B; --on-accent:#0F372F;
  --line:rgba(242,245,242,0.13); --line-strong:rgba(242,245,242,0.26);
  --glow:rgba(236,188,107,0.16);
  --warn:#C89B6A; --warn-bg:rgba(200,155,106,0.14); --warn-border:rgba(200,155,106,0.42);
  --font-head:'Big Shoulders Display',sans-serif;
  --font-body:'Space Grotesk',system-ui,sans-serif;
  --font-mono:'JetBrains Mono',monospace;
}
```

The other 4 themes (`midnight`/`ivory`/`leaf`/`edition`) are the same
variable set with different values — copy verbatim from the `[data-theme=...]`
blocks in any of the 9 `.dc.html` files (they're duplicated identically
across all of them). `ivory`/`leaf` are light themes; the rest are dark.

Two reusable nav components exist in the source and do the same job —
**use `Sleuth Rail.dc.html`** as the ported `NavRail.jsx`/`AppShell.jsx`
basis (it's the one actually wired via `dc-import` into
Dashboard/Indexing/Chat/Settings; `Sleuth Nav.dc.html` looks like an earlier
standalone draft of the same rail and is not imported anywhere — skip it).
The rail is collapsible (272px ↔ 72px), holds the repo switcher, nav items
(Chat/Repos/Indexing status/Settings — GH-only means the repo-switcher's
provider abbreviation badge always reads "GH", so simplify or drop that
badge rather than keeping a dead multi-provider affordance), recent-chat
history (chat page only), and the account menu (theme switcher + log out).

## Global Constraints

- No new pipeline *business* logic. `sleuth/api/` only calls existing
  `store.py`, `ingest/pipeline.py::ingest_repo`, `retrieve/answer.py::stream_answer`.
  The instrumentation callbacks added in Tasks 2/4 are additive (default
  `None`, no behavior change for any existing caller) — not new pipeline
  logic, just observability hooks.
- Indexing progress is kept in an in-process dict (`sleuth/api/progress_store.py`),
  not persisted — resets on backend restart. Acceptable for a local dev tool
  (per design doc Non-Goals: no prod hosting); avoids a queue/worker or a new
  DB table for something that's inherently transient.
- Chat history **is** persisted (`chats`/`messages` tables, Task 3) — decided
  2026-08-19 in favor of surviving page reload, over the simpler ephemeral
  client-state option.
- **Eval stays CLI-only for this plan** (decided 2026-08-24) — no `/eval`
  routes, no Eval screen, no `eval_runs` persistence in the web app. The
  existing `sleuth eval` CLI command and `sleuth/eval/runner.py` are
  untouched. Revisit as its own task if a web Eval screen is wanted later.
- Indexing screen shows **elapsed time**, not the design mockup's fabricated
  "ETA" — there's no reliable way to estimate remaining time from current
  pipeline signals, and inventing one would just be a fake number with a
  precise-looking label. Decided 2026-08-19.
- A chat request against a repo whose `status != 'ready'` is rejected with a
  clear 409, never run against a partial/absent index.
- CORS enabled for the Vite dev server origin only (`http://localhost:5173`),
  `allow_credentials=True` (needed for the session cookie).
- **Auth is real** (see Auth section above) — gatekeeping login via GitHub
  OAuth or email magic link, signed session cookie, no per-user data
  separation (single expected user, schema allows more later).
- No global state library (Redux/Zustand/React Query) and no TypeScript —
  plain `useState`/`useEffect` + `fetch`. `react-router-dom` is the one
  added frontend dependency (routing, not state management).
- Every backend endpoint gets a test using FastAPI's `TestClient` against the
  real test Postgres (existing `tests/conftest.py::pg_conn` fixture), not
  mocked DB calls. External HTTP (Voyage/Groq/GitHub OAuth) is mocked at the
  transport level with `respx`, exactly as `tests/test_answer.py` already
  does — not by mocking client objects. SMTP sending is mocked by monkeypatching
  the send function, not by hitting a real mail server in tests.

---

## File Structure (additions)

```
sleuth/
  api/
    __init__.py
    main.py                # FastAPI() app, CORS, router includes, auth middleware
    schemas.py              # Pydantic request/response models
    progress_store.py       # in-memory per-repo indexing progress
    auth/
      __init__.py
      session.py             # itsdangerous sign/verify, cookie helpers, require_session dep
      github.py               # OAuth code-exchange + profile fetch (raw httpx)
      email_link.py            # magic-link token generation/verification + SMTP send
    routes/
      __init__.py
      auth.py                 # GET /auth/github, GET /auth/github/callback, POST /auth/email, GET /auth/email/verify, POST /auth/logout
      repos.py               # POST/GET /repos, GET /repos/{id}, GET /repos/{id}/progress
      chat.py                 # POST /chats, GET /chats, GET /chats/{id}/messages, POST /chat (SSE)
      users.py                 # GET /me, PATCH /me (theme_preference)
sleuth/ingest/pipeline.py   # modify: ingest_repo(..., on_event=None)
sleuth/ingest/embed.py      # modify: embed_batch(..., on_batch_done=None)
sleuth/retrieve/answer.py   # modify: stream_answer(..., on_sources=None)
sleuth/store.py             # add: get_repo, chat/message CRUD, user CRUD
schema.sql                   # add: users, chats, messages tables
requirements.txt             # + fastapi, uvicorn[standard], itsdangerous
tests/
  test_api_auth.py
  test_api_repos.py
  test_api_chat.py
  (test_pipeline.py, test_embed.py, test_answer.py — extended, not replaced)

web/                      # new Vite React project
  package.json
  vite.config.js
  index.html
  .env.example             # VITE_API_URL
  src/
    main.jsx
    App.jsx                 # react-router-dom routes, RequireAuth wrapper
    theme.css                # design tokens, all 5 themes (see Design System above)
    api.js                    # fetch wrappers (credentials:'include' for session cookie)
    components/
      NavRail.jsx              # ported from Sleuth Rail.dc.html
      AppShell.jsx             # rail + <Outlet/>, shared by Dashboard/Indexing/Chat/Settings
      LandingPage.jsx
      LoginPage.jsx             # GitHub button + email form, ported from Sleuth Login.dc.html
      ConnectRepoScreen.jsx      # ported from Sleuth Connect Repo.dc.html (GitHub-only)
      RepoList.jsx
      AddRepoForm.jsx
      RepoStatusBadge.jsx
      IndexingScreen.jsx
      ChatScreen.jsx
      ChatSidebar.jsx
      MessageList.jsx
      Composer.jsx
      RepoSettingsScreen.jsx
      ThemeSwitcher.jsx
```

---

## Task 0: Auth — users table, session cookies, GitHub OAuth, email magic link

**Files:**
- Create: `sleuth/api/__init__.py`, `sleuth/api/main.py`, `sleuth/api/schemas.py`,
  `sleuth/api/auth/__init__.py`, `sleuth/api/auth/session.py`,
  `sleuth/api/auth/github.py`, `sleuth/api/auth/email_link.py`,
  `sleuth/api/routes/__init__.py`, `sleuth/api/routes/auth.py`,
  `sleuth/api/routes/users.py`
- Modify: `schema.sql` (add `users` table), `sleuth/store.py` (add user CRUD),
  `sleuth/config.py` (add `github_client_id`, `github_client_secret`,
  `session_secret`, `smtp_host`, `smtp_port`, `smtp_username`, `smtp_password`,
  `smtp_from_address`), `requirements.txt` (+ `fastapi`, `uvicorn[standard]`,
  `itsdangerous`)
- Test: `tests/test_api_auth.py`

**Interfaces:**
- Produces: `sleuth.store.get_or_create_user_by_github(conn, github_id, email, name, avatar_url) -> dict`,
  `sleuth.store.get_or_create_user_by_email(conn, email) -> dict`,
  `sleuth.store.get_user(conn, user_id) -> dict | None`,
  `sleuth.store.set_user_theme(conn, user_id, theme) -> None`.
  `sleuth.api.auth.session.create_session_cookie(user_id) -> str`,
  `sleuth.api.auth.session.read_session_cookie(cookie_value) -> str | None` (returns
  user_id or None if missing/expired/tampered), FastAPI dependency
  `require_session(request) -> dict` (raises 401, used by every protected route
  in later tasks). `sleuth.api.auth.github.build_authorize_url(state) -> str`,
  `sleuth.api.auth.github.exchange_code(code) -> dict` (raw `httpx` POST to
  `github.com/login/oauth/access_token` + `GET api.github.com/user`).
  `sleuth.api.auth.email_link.send_magic_link(email, base_url) -> None`,
  `sleuth.api.auth.email_link.verify_magic_link_token(token) -> str | None`
  (returns email or None). Routes: `GET /auth/github`, `GET /auth/github/callback`,
  `POST /auth/email`, `GET /auth/email/verify`, `POST /auth/logout`, `GET /me`,
  `PATCH /me`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_api_auth.py
import httpx
import respx
from fastapi.testclient import TestClient

from sleuth.api.main import create_app
from sleuth.config import Config
from tests.conftest import TEST_DATABASE_URL


def _config():
    return Config(
        voyage_api_key="k", groq_api_key="k", groq_model="m",
        database_url=TEST_DATABASE_URL,
        github_client_id="gh_id", github_client_secret="gh_secret",
        session_secret="test-secret-not-for-prod",
        smtp_host="localhost", smtp_port=1025, smtp_username="u",
        smtp_password="p", smtp_from_address="noreply@example.com",
    )


def test_me_requires_session(pg_conn):
    client = TestClient(create_app(_config()))
    resp = client.get("/me")
    assert resp.status_code == 401


@respx.mock
def test_github_callback_creates_user_and_sets_session(pg_conn):
    respx.post("https://github.com/login/oauth/access_token").mock(
        return_value=httpx.Response(200, json={"access_token": "gh_token"})
    )
    respx.get("https://api.github.com/user").mock(
        return_value=httpx.Response(200, json={
            "id": 12345, "login": "octocat", "name": "The Octocat",
            "email": "octocat@example.com", "avatar_url": "https://avatars/o.png",
        })
    )
    client = TestClient(create_app(_config()))
    resp = client.get("/auth/github/callback", params={"code": "abc", "state": "xyz"})
    assert resp.status_code in (302, 307)
    me = client.get("/me")
    assert me.status_code == 200
    assert me.json()["name"] == "The Octocat"


def test_email_magic_link_round_trip(pg_conn, monkeypatch):
    sent = {}
    def fake_send(email, base_url):
        sent["email"] = email
        sent["base_url"] = base_url
    monkeypatch.setattr("sleuth.api.auth.email_link.send_magic_link", fake_send)

    client = TestClient(create_app(_config()))
    resp = client.post("/auth/email", json={"email": "person@example.com"})
    assert resp.status_code == 200
    assert sent["email"] == "person@example.com"

    from sleuth.api.auth.email_link import _serializer  # test-only reach-in to mint a token
    token = _serializer(client.app.state.config).dumps("person@example.com")
    verify = client.get("/auth/email/verify", params={"token": token})
    assert verify.status_code in (302, 307)
    me = client.get("/me")
    assert me.status_code == 200
    assert me.json()["email"] == "person@example.com"
```

- [ ] **Step 2: Run to verify it fails**

Run: `pytest tests/test_api_auth.py -v`
Expected: FAIL — nothing under `sleuth/api/` exists yet.

- [ ] **Step 3: Add `users` table to `schema.sql`**

```sql
CREATE TABLE IF NOT EXISTS users (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    github_id       bigint UNIQUE,
    email           text UNIQUE,
    name            text,
    avatar_url      text,
    theme_preference text NOT NULL DEFAULT 'storm',
    created_at      timestamptz NOT NULL DEFAULT now()
);
```

- [ ] **Step 4: Add user CRUD to `sleuth/store.py`, config fields to `sleuth/config.py`**

Straightforward `INSERT ... ON CONFLICT DO UPDATE` upserts keyed on
`github_id` / `email` respectively — same raw-SQL style as `create_repo`.
Config fields are plain dataclass fields with `os.environ` fallbacks, same
pattern as the existing `voyage_api_key`/`groq_api_key`.

- [ ] **Step 5: Write `sleuth/api/auth/session.py`**

`itsdangerous.URLSafeTimedSerializer(config.session_secret)` wraps a
`{"user_id": ...}` payload into a signed cookie value, ~30 day max_age on
read. `require_session` is a FastAPI dependency: reads the `sleuth_session`
cookie, verifies it, loads the user via `store.get_user`, raises
`HTTPException(401)` on anything missing/invalid/expired, else returns the
user dict on `request.state`.

- [ ] **Step 6: Write `sleuth/api/auth/github.py`**

`build_authorize_url(state)` builds the `github.com/login/oauth/authorize`
URL with `client_id`, `redirect_uri`, `scope=read:user user:email`, `state`
(CSRF token, stored in a short-lived signed cookie, checked on callback).
`exchange_code(code, config)` does the two raw `httpx` calls described above
and returns the normalized profile dict.

- [ ] **Step 7: Write `sleuth/api/auth/email_link.py`**

`send_magic_link(email, base_url, config)` mints a signed token via
`itsdangerous.URLSafeTimedSerializer(config.session_secret, salt="magic-link")`,
builds `{base_url}/auth/email/verify?token=...`, sends via `smtplib.SMTP`
against `config.smtp_host`/`smtp_port` with STARTTLS + login (AWS SES SMTP
credentials in prod, a local Mailpit/MailHog catcher in dev — same env-var
switch as everything else in `config.py`). `verify_magic_link_token(token,
config, max_age=900)` decodes and returns the email, or `None` on
expiry/tamper.

- [ ] **Step 8: Write `sleuth/api/routes/auth.py`, `sleuth/api/routes/users.py`**

`GET /auth/github` redirects to `build_authorize_url`. `GET /auth/github/callback`
validates `state`, calls `exchange_code`, upserts the user via
`store.get_or_create_user_by_github`, sets the session cookie, redirects to
the frontend's post-login route (`{FRONTEND_URL}/repos` or similar — new
`Config.frontend_url` field). `POST /auth/email` body `{email}`, calls
`send_magic_link`, always returns 200 (don't leak whether an email exists —
not that it matters much for a personal tool, but it's the correct shape).
`GET /auth/email/verify?token=...` verifies, upserts via
`store.get_or_create_user_by_email`, sets session cookie, redirects.
`POST /auth/logout` clears the cookie. `GET /me` returns the current user
(via `require_session`). `PATCH /me` body `{theme_preference}` calls
`store.set_user_theme`.

- [ ] **Step 9: Write `sleuth/api/main.py`**

`create_app(config)` wires `CORSMiddleware` (`allow_credentials=True`,
origin `http://localhost:5173`), includes the `auth` and `users` routers
(unprotected), stores `config` on `app.state`, and — same
`get_connection`/`apply_schema`-per-request middleware pattern as the old
Task 1 draft below, kept as-is since it already works and every later task's
routes build on it.

- [ ] **Step 10: Run to verify it passes**

Run: `pytest tests/test_api_auth.py -v`
Expected: PASS

- [ ] **Step 11: Commit**

```bash
git add schema.sql sleuth/store.py sleuth/config.py sleuth/api requirements.txt tests/test_api_auth.py
git commit -m "feat: add GitHub OAuth + email magic-link auth"
```

---

## Task 1: Store helper + repo endpoints (behind auth)

> Everything below inherits Task 0's `require_session` dependency — every
> route added from here on takes `user=Depends(require_session)` (or the
> project's equivalent pattern) unless explicitly noted otherwise. The route
> bodies/tests below are otherwise unchanged from the original draft.

**Files:**
- Modify: `sleuth/store.py` (add `get_repo`)
- Create: `sleuth/api/__init__.py`, `sleuth/api/main.py`, `sleuth/api/schemas.py`, `sleuth/api/routes/__init__.py`, `sleuth/api/routes/repos.py`
- Update: `requirements.txt` (+ `fastapi`, `uvicorn[standard]`)
- Test: `tests/test_api_repos.py`

**Interfaces:**
- Consumes: `sleuth.store.create_repo(conn, github_url) -> str`, `sleuth.store.list_repos(conn) -> list[tuple[str,str,str]]`, `sleuth.ingest.pipeline.ingest_repo(github_url, conn, config) -> str`, `sleuth.config.load_config() -> Config`, `sleuth.db.get_connection(url)`, `sleuth.db.apply_schema(conn)`.
- Produces: `sleuth.store.get_repo(conn, repo_id) -> dict | None` with keys `id, github_url, status, error_message, embedding_model, embedding_dim` — used by every later task that needs a single repo's status. `RepoOut` Pydantic schema with the same fields, used by Tasks 1-5. `POST /repos`, `GET /repos`, `GET /repos/{id}` routes.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_api_repos.py
import time

import httpx
import pytest
import respx
from fastapi.testclient import TestClient

from sleuth.api.main import create_app
from sleuth.config import Config
from tests.conftest import TEST_DATABASE_URL


def _client():
    config = Config(voyage_api_key="k", groq_api_key="k", groq_model="m", database_url=TEST_DATABASE_URL)
    return TestClient(create_app(config))


def test_get_unknown_repo_returns_404(pg_conn):
    resp = _client().get("/repos/00000000-0000-0000-0000-000000000000")
    assert resp.status_code == 404


@respx.mock
def test_add_list_get_repo_round_trip(pg_conn):
    respx.post("https://api.voyageai.com/v1/embeddings").mock(
        return_value=httpx.Response(200, json={"data": []})
    )
    client = _client()

    resp = client.post("/repos", json={"github_url": "https://github.com/example/repo"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["github_url"] == "https://github.com/example/repo"
    assert body["status"] == "pending"
    repo_id = body["id"]

    listed = client.get("/repos").json()
    assert any(r["id"] == repo_id for r in listed)

    for _ in range(50):
        got = client.get(f"/repos/{repo_id}").json()
        if got["status"] in ("ready", "failed"):
            break
        time.sleep(0.1)
    assert got["status"] in ("ready", "failed")
```

- [ ] **Step 2: Run to verify it fails**

Run: `pytest tests/test_api_repos.py -v`
Expected: FAIL — `sleuth.api` doesn't exist yet.

- [ ] **Step 3: Add `get_repo` to `sleuth/store.py`**

```python
def get_repo(conn: psycopg.Connection, repo_id: str) -> dict | None:
    row = conn.execute(
        "SELECT id, github_url, status, error_message, embedding_model, embedding_dim "
        "FROM repos WHERE id = %s",
        (repo_id,),
    ).fetchone()
    if row is None:
        return None
    return {
        "id": str(row[0]),
        "github_url": row[1],
        "status": row[2],
        "error_message": row[3],
        "embedding_model": row[4],
        "embedding_dim": row[5],
    }
```

- [ ] **Step 4: Write `sleuth/api/schemas.py`**

```python
from pydantic import BaseModel


class RepoOut(BaseModel):
    id: str
    github_url: str
    status: str
    error_message: str | None = None
    embedding_model: str | None = None
    embedding_dim: int | None = None


class AddRepoIn(BaseModel):
    github_url: str
```

- [ ] **Step 5: Write `sleuth/api/routes/repos.py`**

```python
from fastapi import APIRouter, BackgroundTasks, HTTPException, Request

from sleuth.api.schemas import AddRepoIn, RepoOut
from sleuth.db import get_connection
from sleuth.ingest.pipeline import ingest_repo
from sleuth.store import create_repo, get_repo, list_repos

router = APIRouter()


async def _run_ingest(github_url: str, database_url: str, config) -> None:
    conn = get_connection(database_url)
    try:
        await ingest_repo(github_url, conn, config)
    finally:
        conn.close()


@router.post("/repos", response_model=RepoOut)
def add_repo(body: AddRepoIn, request: Request, background_tasks: BackgroundTasks) -> RepoOut:
    conn = request.state.conn
    config = request.state.config
    repo_id = create_repo(conn, body.github_url)
    conn.commit()
    background_tasks.add_task(_run_ingest, body.github_url, config.database_url, config)
    return RepoOut(**get_repo(conn, repo_id))


@router.get("/repos", response_model=list[RepoOut])
def get_repos(request: Request) -> list[RepoOut]:
    conn = request.state.conn
    return [
        RepoOut(**get_repo(conn, repo_id))
        for repo_id, _github_url, _status in list_repos(conn)
    ]


@router.get("/repos/{repo_id}", response_model=RepoOut)
def get_repo_by_id(repo_id: str, request: Request) -> RepoOut:
    repo = get_repo(request.state.conn, repo_id)
    if repo is None:
        raise HTTPException(status_code=404, detail="repo not found")
    return RepoOut(**repo)
```

- [ ] **Step 6: Write `sleuth/api/main.py`**

```python
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from sleuth.api.routes import repos
from sleuth.config import Config, load_config
from sleuth.db import apply_schema, get_connection


def create_app(config: Config | None = None) -> FastAPI:
    config = config or load_config()
    app = FastAPI(title="Sleuth API")

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:5173"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.middleware("http")
    async def attach_conn(request, call_next):
        conn = get_connection(config.database_url)
        apply_schema(conn)
        request.state.conn = conn
        request.state.config = config
        try:
            return await call_next(request)
        finally:
            conn.close()

    app.include_router(repos.router)
    return app


app = create_app()
```

- [ ] **Step 7: Run to verify it passes**

Run: `pytest tests/test_api_repos.py -v`
Expected: PASS

- [ ] **Step 8: Commit**

```bash
git add sleuth/store.py sleuth/api requirements.txt tests/test_api_repos.py
git commit -m "feat: add FastAPI scaffolding and repo endpoints"
```

---

## Task 2: Indexing progress instrumentation + `GET /repos/{id}/progress`

**Files:**
- Modify: `sleuth/ingest/embed.py` (`VoyageEmbedder.embed_batch` gains `on_batch_done`)
- Modify: `sleuth/ingest/pipeline.py` (`ingest_repo` gains `on_event`)
- Create: `sleuth/api/progress_store.py`
- Modify: `sleuth/api/routes/repos.py` (wire `on_event` into the background task, add `GET /repos/{id}/progress`)
- Test: extend `tests/test_embed.py`, `tests/test_pipeline.py`; create `tests/test_api_repos.py::test_progress_endpoint...` (append to existing file)

**Interfaces:**
- Consumes: Task 1's `RepoOut`/routing setup.
- Produces: `progress_store.start(repo_id)`, `progress_store.record(repo_id, step, **detail)`, `progress_store.get(repo_id) -> dict | None` (keys `step, detail, log, elapsed_seconds`) — consumed by Task 9 (Indexing screen). `embed_batch(texts, on_batch_done: Callable[[int, int], None] | None = None)`. `ingest_repo(github_url, conn, config, on_event: Callable[[str, dict], None] | None = None)`.

- [ ] **Step 1: Write the failing tests**

```python
# append to tests/test_embed.py
@pytest.mark.asyncio
@respx.mock
async def test_embed_batch_reports_progress_via_callback():
    respx.post("https://api.voyageai.com/v1/embeddings").mock(
        return_value=httpx.Response(200, json={"data": [{"embedding": [0.1] * 1024, "index": 0}]})
    )
    embedder = VoyageEmbedder(api_key="k", batch_size=1)
    calls = []
    await embedder.embed_batch(["a", "b"], on_batch_done=lambda done, total: calls.append((done, total)))

    assert len(calls) == 2
    assert all(total == 2 for _done, total in calls)
    assert {done for done, _total in calls} == {1, 2}
```

```python
# append to tests/test_pipeline.py
@pytest.mark.asyncio
@respx.mock
async def test_ingest_repo_emits_progress_events(pg_conn, tmp_git_repo):
    respx.post("https://api.voyageai.com/v1/embeddings").mock(
        return_value=httpx.Response(200, json={"data": [{"embedding": [0.1] * 1024, "index": 0}]})
    )
    config = Config(voyage_api_key="k", groq_api_key="k", groq_model="m", database_url="unused")
    events = []
    await ingest_repo(str(tmp_git_repo), pg_conn, config, on_event=lambda step, detail: events.append((step, detail)))

    steps = [step for step, _detail in events]
    assert "cloned" in steps
    assert "ready" in steps
```

Use whatever local-repo fixture `tests/test_pipeline.py` already defines for clone-from-disk (check the existing file for the fixture name — reuse it rather than adding a second one).

- [ ] **Step 2: Run to verify it fails**

Run: `pytest tests/test_embed.py tests/test_pipeline.py -v -k progress`
Expected: FAIL — `on_batch_done`/`on_event` not accepted yet.

- [ ] **Step 3: Add `on_batch_done` to `embed_batch`**

```python
async def embed_batch(self, texts: list[str], on_batch_done=None) -> list[list[float]]:
    batches = [texts[i : i + self.batch_size] for i in range(0, len(texts), self.batch_size)]
    semaphore = asyncio.Semaphore(self.max_concurrency)
    total = len(batches)
    completed = 0

    async def run_batch(batch):
        nonlocal completed
        async with semaphore:
            vectors = await self._embed_one_batch_impl(batch)
        completed += 1
        if on_batch_done:
            on_batch_done(completed, total)
        return vectors

    async with httpx.AsyncClient() as client:
        self._client = client
        results = await asyncio.gather(*(self._run_batch_with_client(client, semaphore, batch, on_batch_done, total) for batch in batches))

    vectors: list[list[float]] = []
    for batch_vectors in results:
        vectors.extend(batch_vectors)
    return vectors
```

This needs the semaphore-guarded HTTP call kept inside the client context, so restructure `_embed_one_batch` into a version that also fires the callback on completion, without changing its request/response handling:

```python
    async def _run_batch_with_client(self, client, semaphore, batch, on_batch_done, total):
        async with semaphore:
            vectors = await self._embed_one_batch(client, semaphore, batch)
        if on_batch_done:
            on_batch_done(getattr(self, "_completed", 0) + 1, total)
        return vectors
```

Simplify: track `completed` via a mutable counter shared across the gathered coroutines (a single-element list, since Python closures can't rebind an outer int without `nonlocal`, and `nonlocal` works fine here since there's no nested `async def` inside another `async def` beyond one level):

```python
    async def embed_batch(self, texts: list[str], on_batch_done=None) -> list[list[float]]:
        batches = [texts[i : i + self.batch_size] for i in range(0, len(texts), self.batch_size)]
        semaphore = asyncio.Semaphore(self.max_concurrency)
        total = len(batches)
        completed = 0

        async def run_one(client, batch):
            nonlocal completed
            vectors = await self._embed_one_batch(client, semaphore, batch)
            completed += 1
            if on_batch_done:
                on_batch_done(completed, total)
            return vectors

        async with httpx.AsyncClient() as client:
            results = await asyncio.gather(*(run_one(client, batch) for batch in batches))

        vectors: list[list[float]] = []
        for batch_vectors in results:
            vectors.extend(batch_vectors)
        return vectors
```

`_embed_one_batch` is unchanged — it still acquires `semaphore` internally, so concurrency is still bounded by `max_concurrency`. `completed` incrementing outside the semaphore-guarded section is fine since `asyncio` coroutines are single-threaded; no lock needed.

- [ ] **Step 4: Write `sleuth/api/progress_store.py`**

```python
import time
from threading import Lock

_progress: dict[str, dict] = {}
_lock = Lock()


def start(repo_id: str) -> None:
    with _lock:
        _progress[repo_id] = {"step": "cloning", "detail": {}, "log": [], "started_at": time.monotonic()}


def record(repo_id: str, step: str, **detail) -> None:
    with _lock:
        entry = _progress.setdefault(
            repo_id, {"step": step, "detail": {}, "log": [], "started_at": time.monotonic()}
        )
        entry["step"] = step
        entry["detail"] = detail
        entry["log"].append({"step": step, **detail})
        entry["log"] = entry["log"][-20:]


def get(repo_id: str) -> dict | None:
    with _lock:
        entry = _progress.get(repo_id)
        if entry is None:
            return None
        return {
            "step": entry["step"],
            "detail": entry["detail"],
            "log": entry["log"],
            "elapsed_seconds": time.monotonic() - entry["started_at"],
        }
```

- [ ] **Step 5: Add `on_event` to `ingest_repo`**

```python
async def ingest_repo(github_url: str, conn, config: Config, on_event=None) -> str:
    def emit(step: str, **detail) -> None:
        if on_event:
            on_event(step, detail)

    repo_id = _find_or_create_repo(conn, github_url)
    update_repo_status(conn, repo_id, "indexing")
    conn.commit()
    emit("cloning")

    embedder = VoyageEmbedder(api_key=config.voyage_api_key)

    workdir = tempfile.mkdtemp(prefix="sleuth-clone-")
    try:
        try:
            repo_path = clone_repo(github_url, workdir)
        except CloneError as exc:
            update_repo_status(conn, repo_id, "failed", str(exc))
            conn.commit()
            emit("failed", error=str(exc))
            return repo_id

        files = list_source_files(repo_path, SUPPORTED_EXTENSIONS)
        emit("cloned", files=len(files))

        all_chunks = []
        skipped = 0
        for file_path in files:
            relative_path = str(file_path.relative_to(repo_path))
            source_bytes = file_path.read_bytes()
            try:
                chunks = chunk_source(source_bytes, relative_path, file_path.suffix)
            except Exception:
                skipped += 1
                continue
            all_chunks.extend(chunks)
        emit("parsed", parsed=len(files) - skipped, skipped=skipped)
        emit("chunked", chunks=len(all_chunks))

        current_keys = {(c.file_path, c.symbol_name) for c in all_chunks}
        existing_hashes = get_existing_hashes(conn, repo_id)

        to_embed = [
            c for c in all_chunks
            if existing_hashes.get((c.file_path, c.symbol_name)) != c.content_hash
        ]

        if to_embed:
            texts = [
                format_chunk_context(c, EXTENSION_TO_LANGUAGE.get("." + c.file_path.rsplit(".", 1)[-1], ""))
                for c in to_embed
            ]
            emit("embedding_start", to_embed=len(to_embed))
            vectors = await embedder.embed_batch(
                texts,
                on_batch_done=lambda done, total: emit("embedding_progress", done=done, total=total),
            )
            upsert_chunks(conn, repo_id, list(zip(to_embed, vectors)))
            conn.commit()

        set_repo_embedding_info(conn, repo_id, embedder.model_name, embedder.dim)
        conn.commit()

        delete_stale_chunks(conn, repo_id, current_keys)
        conn.commit()
        emit("stored", upserted=len(to_embed), skipped_unchanged=len(all_chunks) - len(to_embed))

        update_repo_status(conn, repo_id, "ready")
        conn.commit()
        emit("ready")
        return repo_id
    finally:
        shutil.rmtree(workdir, ignore_errors=True)
```

- [ ] **Step 6: Wire progress into the API route and add the endpoint**

In `sleuth/api/routes/repos.py`:

```python
from sleuth.api import progress_store


async def _run_ingest(repo_id: str, github_url: str, database_url: str, config) -> None:
    conn = get_connection(database_url)
    progress_store.start(repo_id)
    try:
        await ingest_repo(
            github_url, conn, config,
            on_event=lambda step, detail: progress_store.record(repo_id, step, **detail),
        )
    finally:
        conn.close()


@router.post("/repos", response_model=RepoOut)
def add_repo(body: AddRepoIn, request: Request, background_tasks: BackgroundTasks) -> RepoOut:
    conn = request.state.conn
    config = request.state.config
    repo_id = create_repo(conn, body.github_url)
    conn.commit()
    background_tasks.add_task(_run_ingest, repo_id, body.github_url, config.database_url, config)
    return RepoOut(**get_repo(conn, repo_id))


@router.get("/repos/{repo_id}/progress")
def get_progress(repo_id: str, request: Request) -> dict:
    repo = get_repo(request.state.conn, repo_id)
    if repo is None:
        raise HTTPException(status_code=404, detail="repo not found")
    progress = progress_store.get(repo_id)
    if progress is None:
        return {"step": repo["status"], "detail": {}, "log": [], "elapsed_seconds": 0}
    return progress
```

- [ ] **Step 7: Run to verify it passes**

Run: `pytest tests/test_embed.py tests/test_pipeline.py tests/test_api_repos.py -v`
Expected: PASS, all existing tests in those files still green (no signature broke — every new parameter defaults to `None`).

- [ ] **Step 8: Commit**

```bash
git add sleuth/ingest/embed.py sleuth/ingest/pipeline.py sleuth/api/progress_store.py sleuth/api/routes/repos.py tests/test_embed.py tests/test_pipeline.py
git commit -m "feat: add indexing progress instrumentation and endpoint"
```

---

## Task 3: Chat persistence schema + chat CRUD endpoints

**Files:**
- Modify: `schema.sql` (add `chats`, `messages` tables)
- Modify: `sleuth/store.py` (add chat/message CRUD)
- Modify: `sleuth/api/schemas.py` (add `ChatOut`, `MessageOut`, `CreateChatIn`)
- Create: `sleuth/api/routes/chat.py` (CRUD part only — SSE endpoint is Task 4)
- Modify: `sleuth/api/main.py` (include the new router)
- Test: `tests/test_api_chat.py`

**Interfaces:**
- Consumes: Task 1's `get_repo`, `RepoOut` pattern.
- Produces: `store.create_chat(conn, repo_id, title="New chat") -> str`, `store.list_chats(conn, repo_id) -> list[dict]` (keys `id, title, created_at, message_count`), `store.get_chat(conn, chat_id) -> dict | None` (keys `id, repo_id, title`), `store.create_message(conn, chat_id, role, content, sources=None) -> str`, `store.list_messages(conn, chat_id) -> list[dict]` (keys `id, role, content, sources, created_at`). Routes `POST /chats`, `GET /chats`, `GET /chats/{id}/messages` — consumed by Task 4 (SSE endpoint reuses `create_message`) and Task 10 (Chat screen).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_api_chat.py
from fastapi.testclient import TestClient

from sleuth.api.main import create_app
from sleuth.config import Config
from sleuth.store import create_repo, update_repo_status
from tests.conftest import TEST_DATABASE_URL


def _client():
    config = Config(voyage_api_key="k", groq_api_key="k", groq_model="m", database_url=TEST_DATABASE_URL)
    return TestClient(create_app(config))


def test_create_chat_requires_ready_repo(pg_conn):
    repo_id = create_repo(pg_conn, "https://github.com/example/repo")
    pg_conn.commit()  # status defaults to pending
    resp = _client().post("/chats", json={"repo_id": repo_id})
    assert resp.status_code == 409


def test_create_list_chat_and_messages_round_trip(pg_conn):
    repo_id = create_repo(pg_conn, "https://github.com/example/repo")
    update_repo_status(pg_conn, repo_id, "ready")
    pg_conn.commit()

    client = _client()
    created = client.post("/chats", json={"repo_id": repo_id}).json()
    assert created["title"] == "New chat"

    listed = client.get(f"/chats?repo_id={repo_id}").json()
    assert listed[0]["id"] == created["id"]
    assert listed[0]["message_count"] == 0

    messages = client.get(f"/chats/{created['id']}/messages").json()
    assert messages == []
```

- [ ] **Step 2: Run to verify it fails**

Run: `pytest tests/test_api_chat.py -v`
Expected: FAIL — `/chats` route doesn't exist.

- [ ] **Step 3: Add schema tables**

Append to `schema.sql`:

```sql
CREATE TABLE IF NOT EXISTS chats (
    id         uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    repo_id    uuid NOT NULL REFERENCES repos(id) ON DELETE CASCADE,
    title      text NOT NULL DEFAULT 'New chat',
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS messages (
    id         uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    chat_id    uuid NOT NULL REFERENCES chats(id) ON DELETE CASCADE,
    role       text NOT NULL CHECK (role IN ('user', 'assistant')),
    content    text NOT NULL,
    sources    jsonb,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS messages_chat_idx ON messages (chat_id, created_at);
```

- [ ] **Step 4: Add CRUD to `sleuth/store.py`**

```python
import json


def create_chat(conn: psycopg.Connection, repo_id: str, title: str = "New chat") -> str:
    row = conn.execute(
        "INSERT INTO chats (repo_id, title) VALUES (%s, %s) RETURNING id", (repo_id, title)
    ).fetchone()
    return str(row[0])


def list_chats(conn: psycopg.Connection, repo_id: str) -> list[dict]:
    rows = conn.execute(
        """
        SELECT c.id, c.title, c.created_at, COUNT(m.id)
        FROM chats c LEFT JOIN messages m ON m.chat_id = c.id
        WHERE c.repo_id = %s
        GROUP BY c.id
        ORDER BY c.created_at DESC
        """,
        (repo_id,),
    ).fetchall()
    return [
        {"id": str(cid), "title": title, "created_at": created_at.isoformat(), "message_count": count}
        for cid, title, created_at, count in rows
    ]


def get_chat(conn: psycopg.Connection, chat_id: str) -> dict | None:
    row = conn.execute("SELECT id, repo_id, title FROM chats WHERE id = %s", (chat_id,)).fetchone()
    if row is None:
        return None
    return {"id": str(row[0]), "repo_id": str(row[1]), "title": row[2]}


def create_message(
    conn: psycopg.Connection, chat_id: str, role: str, content: str, sources: list[dict] | None = None
) -> str:
    row = conn.execute(
        "INSERT INTO messages (chat_id, role, content, sources) VALUES (%s, %s, %s, %s) RETURNING id",
        (chat_id, role, content, json.dumps(sources) if sources is not None else None),
    ).fetchone()
    return str(row[0])


def list_messages(conn: psycopg.Connection, chat_id: str) -> list[dict]:
    rows = conn.execute(
        "SELECT id, role, content, sources, created_at FROM messages WHERE chat_id = %s ORDER BY created_at",
        (chat_id,),
    ).fetchall()
    return [
        {"id": str(mid), "role": role, "content": content, "sources": sources, "created_at": created_at.isoformat()}
        for mid, role, content, sources, created_at in rows
    ]
```

- [ ] **Step 5: Add schemas**

Append to `sleuth/api/schemas.py`:

```python
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
```

- [ ] **Step 6: Write `sleuth/api/routes/chat.py`**

```python
from fastapi import APIRouter, HTTPException, Request

from sleuth.api.schemas import ChatOut, CreateChatIn, MessageOut
from sleuth.store import create_chat, get_chat, get_repo, list_chats, list_messages

router = APIRouter()


@router.post("/chats", response_model=ChatOut)
def create_chat_route(body: CreateChatIn, request: Request) -> ChatOut:
    conn = request.state.conn
    repo = get_repo(conn, body.repo_id)
    if repo is None:
        raise HTTPException(status_code=404, detail="repo not found")
    if repo["status"] != "ready":
        raise HTTPException(status_code=409, detail=f"repo is {repo['status']}, not ready")
    chat_id = create_chat(conn, body.repo_id)
    conn.commit()
    return ChatOut(**[c for c in list_chats(conn, body.repo_id) if c["id"] == chat_id][0])


@router.get("/chats", response_model=list[ChatOut])
def get_chats_route(repo_id: str, request: Request) -> list[ChatOut]:
    return [ChatOut(**c) for c in list_chats(request.state.conn, repo_id)]


@router.get("/chats/{chat_id}/messages", response_model=list[MessageOut])
def get_messages_route(chat_id: str, request: Request) -> list[MessageOut]:
    conn = request.state.conn
    if get_chat(conn, chat_id) is None:
        raise HTTPException(status_code=404, detail="chat not found")
    return [MessageOut(**m) for m in list_messages(conn, chat_id)]
```

- [ ] **Step 7: Register the router**

In `sleuth/api/main.py`:

```python
from sleuth.api.routes import chat, repos
...
    app.include_router(repos.router)
    app.include_router(chat.router)
```

- [ ] **Step 8: Run to verify it passes**

Run: `pytest tests/test_api_chat.py -v`
Expected: PASS

- [ ] **Step 9: Commit**

```bash
git add schema.sql sleuth/store.py sleuth/api tests/test_api_chat.py
git commit -m "feat: add chat/message persistence and CRUD endpoints"
```

---

## Task 4: Chat SSE streaming endpoint

**Files:**
- Modify: `sleuth/retrieve/answer.py` (`stream_answer` gains `on_sources`)
- Modify: `sleuth/api/routes/chat.py` (add `POST /chat`)
- Test: extend `tests/test_answer.py`; append to `tests/test_api_chat.py`

**Interfaces:**
- Consumes: Task 3's `create_message`, `get_chat`.
- Produces: `stream_answer(question, repo_id, conn, config, on_sources: Callable[[list[SearchResult]], None] | None = None)`. `POST /chat` body `{chat_id, question}` → SSE response: one `event: sources` frame (JSON list of `{file_path, symbol_name, kind, start_line, end_line}`), then per-token `data:` frames, then `event: done`. Consumed by Task 10 (Chat screen's `streamChat`).

- [ ] **Step 1: Write the failing test**

```python
# append to tests/test_answer.py
@pytest.mark.asyncio
@respx.mock
async def test_stream_answer_reports_sources_via_callback(pg_conn):
    repo_id = create_repo(pg_conn, "https://github.com/example/repo")
    update_repo_status(pg_conn, repo_id, "ready")
    upsert_chunks(
        pg_conn, repo_id,
        [(Chunk("f.py", "foo", "function", 1, 2, "def foo():\n    return 1\n"), [0.1] * 1024)],
    )
    pg_conn.commit()

    respx.post("https://api.voyageai.com/v1/embeddings").mock(
        return_value=httpx.Response(200, json={"data": [{"embedding": [0.1] * 1024, "index": 0}]})
    )
    respx.post("https://api.groq.com/openai/v1/chat/completions").mock(
        return_value=httpx.Response(200, content=b'data: {"choices":[{"delta":{"content":"hi"}}]}\n\ndata: [DONE]\n\n')
    )

    config = Config(voyage_api_key="k", groq_api_key="k", groq_model="m", database_url="unused")
    captured = []
    tokens = [t async for t in stream_answer("q?", repo_id, pg_conn, config, on_sources=lambda results: captured.append(results))]

    assert "".join(tokens) == "hi"
    assert len(captured) == 1
    assert captured[0][0].file_path == "f.py"
```

```python
# append to tests/test_api_chat.py
@respx.mock
def test_post_chat_streams_tokens_and_persists_messages(pg_conn):
    repo_id = create_repo(pg_conn, "https://github.com/example/repo")
    update_repo_status(pg_conn, repo_id, "ready")
    upsert_chunks(
        pg_conn, repo_id,
        [(Chunk("f.py", "foo", "function", 1, 2, "def foo():\n    return 1\n"), [0.1] * 1024)],
    )
    pg_conn.commit()

    respx.post("https://api.voyageai.com/v1/embeddings").mock(
        return_value=httpx.Response(200, json={"data": [{"embedding": [0.1] * 1024, "index": 0}]})
    )
    respx.post("https://api.groq.com/openai/v1/chat/completions").mock(
        return_value=httpx.Response(200, content=b'data: {"choices":[{"delta":{"content":"hi"}}]}\n\ndata: [DONE]\n\n')
    )

    client = _client()
    chat_id = client.post("/chats", json={"repo_id": repo_id}).json()["id"]

    with client.stream("POST", "/chat", json={"chat_id": chat_id, "question": "what does foo do?"}) as resp:
        body = "".join(resp.iter_text())

    assert "event: sources" in body
    assert "event: done" in body

    messages = client.get(f"/chats/{chat_id}/messages").json()
    assert [m["role"] for m in messages] == ["user", "assistant"]
    assert messages[1]["content"] == "hi"
    assert messages[1]["sources"][0]["file_path"] == "f.py"


def test_post_chat_rejects_not_ready_repo(pg_conn):
    repo_id = create_repo(pg_conn, "https://github.com/example/repo")
    pg_conn.commit()
    client = _client()
    resp = client.post("/chats", json={"repo_id": repo_id})
    assert resp.status_code == 409  # can't even create a chat yet — covered by Task 3's test
```

- [ ] **Step 2: Run to verify it fails**

Run: `pytest tests/test_answer.py tests/test_api_chat.py -v -k "sources or streams_tokens"`
Expected: FAIL

- [ ] **Step 3: Add `on_sources` to `stream_answer`**

```python
async def stream_answer(question: str, repo_id: str, conn, config: Config, on_sources=None) -> AsyncIterator[str]:
    row = conn.execute("SELECT status FROM repos WHERE id = %s", (repo_id,)).fetchone()
    if row is None or row[0] != "ready":
        raise ValueError(f"Repo {repo_id} is not ready to query (status={row[0] if row else 'missing'})")

    embedder = VoyageEmbedder(api_key=config.voyage_api_key)
    query_vector = (await embedder.embed_batch([question]))[0]
    results = search_chunks(conn, repo_id, query_vector)
    if on_sources:
        on_sources(results)
    prompt = build_prompt(question, results)

    chain = get_fallback_chain(config)
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": prompt},
    ]
    async for token in chat_with_fallback(chain, messages, stream=True):
        yield token
```

- [ ] **Step 4: Add `POST /chat` to `sleuth/api/routes/chat.py`**

```python
import json

from fastapi.responses import StreamingResponse

from sleuth.retrieve.answer import stream_answer
from sleuth.store import create_message


class SendMessageIn(BaseModel):
    chat_id: str
    question: str


@router.post("/chat")
async def post_chat(body: SendMessageIn, request: Request) -> StreamingResponse:
    conn = request.state.conn
    config = request.state.config
    chat = get_chat(conn, body.chat_id)
    if chat is None:
        raise HTTPException(status_code=404, detail="chat not found")
    repo = get_repo(conn, chat["repo_id"])
    if repo["status"] != "ready":
        raise HTTPException(status_code=409, detail=f"repo is {repo['status']}, not ready")

    create_message(conn, body.chat_id, "user", body.question)
    conn.commit()

    async def event_stream():
        collected_sources: list[dict] = []

        def on_sources(results):
            collected_sources.extend(
                {
                    "file_path": r.file_path,
                    "symbol_name": r.symbol_name,
                    "kind": r.kind,
                    "start_line": r.start_line,
                    "end_line": r.end_line,
                }
                for r in results
            )
            yield_sources_event = f"event: sources\ndata: {json.dumps(collected_sources)}\n\n"
            chunks.append(yield_sources_event)

        chunks: list[str] = []
        answer_parts: list[str] = []
        async for token in stream_answer(body.question, chat["repo_id"], conn, config, on_sources=on_sources):
            for pending in chunks:
                yield pending
            chunks.clear()
            answer_parts.append(token)
            yield f"data: {token}\n\n"

        create_message(conn, body.chat_id, "assistant", "".join(answer_parts), sources=collected_sources)
        conn.commit()
        yield "event: done\ndata: {}\n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream")
```

`SendMessageIn` needs `from pydantic import BaseModel` imported at the top of the
file alongside the existing `ChatOut`/`CreateChatIn` imports from `schemas.py` —
either add it to `schemas.py` next to the others (preferred, keeps all request
models together) or import `BaseModel` directly in `chat.py`. Use `schemas.py`
for consistency with Tasks 1 and 3.

- [ ] **Step 5: Run to verify it passes**

Run: `pytest tests/test_answer.py tests/test_api_chat.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add sleuth/retrieve/answer.py sleuth/api/routes/chat.py sleuth/api/schemas.py tests/test_answer.py tests/test_api_chat.py
git commit -m "feat: add SSE chat endpoint with source citations and persistence"
```

---

## Task 5: Vite scaffold + design tokens + routing shell

**Files:**
- Create: `web/` (Vite React scaffold via `npm create vite@latest web -- --template react`)
- Create: `web/src/theme.css`, `web/src/api.js`, `web/src/App.jsx`, `web/src/components/NavRail.jsx`, `web/src/components/AppShell.jsx`
- Create (stub placeholders, filled in by Tasks 6/8/9/10 + a later Repo Settings task): `web/src/components/LandingPage.jsx`, `RepoList.jsx`, `IndexingScreen.jsx`, `ChatScreen.jsx`, `RepoSettingsScreen.jsx`
- Create: `web/.env.example`

**Interfaces:**
- Consumes: nothing from `sleuth/` yet (backend only reached via `fetch` in Task 8+).
- Produces: CSS custom properties from the Design System section above, importable by every later component. `AppShell` renders `<NavRail/>` + `<Outlet/>` — every app screen (Tasks 6, 8-10, and the follow-on Repo Settings task) renders inside it. `api.js` exports the base `fetch` wrapper (`apiUrl(path)`) other tasks build on.

- [ ] **Step 1: Scaffold Vite**

Run (Windows/Git Bash, `web/` doesn't exist yet):
```bash
npm create vite@latest web -- --template react
cd web && npm install react-router-dom
```

- [ ] **Step 2: Write `web/.env.example`**

```
VITE_API_URL=http://localhost:8000
```

- [ ] **Step 3: Write `web/src/theme.css`**

Semantic variable names (`--panel`, `--text-secondary`, `--status-ready`,
etc.) are kept stable from the original draft below — every downstream
component in Tasks 8-10 references them — but retargeted to the real
`storm` theme's palette from the Design System section, with the other 4
themes (`midnight`/`ivory`/`leaf`/`edition`, added for real in Task 11)
as `[data-theme="..."]` override blocks layered on top of the same
variable names. This is the mapping from the new design's raw tokens
(`--bg`, `--deep`, `--surface`, `--accent`, `--line`, `--warn`, ...) onto
the semantic names the components already use:

```css
:root, [data-theme="storm"] {
  --bg: #0F372F;
  --panel: #143F36;            /* was --surface */
  --panel-alt: #0A2621;        /* was --deep */
  --text: #F2F5F2;
  --text-secondary: rgba(242,245,242,0.75);
  --text-muted: rgba(242,245,242,0.58);
  --text-faint: rgba(242,245,242,0.30);
  --border: rgba(242,245,242,0.13);       /* was --line */
  --border-strong: rgba(242,245,242,0.26); /* was --line-strong */
  --accent: #ECBC6B;
  --accent-hover: #F0C97E;
  --accent-wash: rgba(236,188,107,0.16);  /* was --glow */
  --accent-on: #0F372F;                    /* was --on-accent */
  --status-ready: #ECBC6B;
  --status-ready-wash: rgba(236,188,107,0.16);
  --status-neutral: #C89B6A;               /* was --warn */
  --status-neutral-wash: rgba(200,155,106,0.14); /* was --warn-bg */
  --font-mono: 'JetBrains Mono', monospace;
  --font-sans: 'Space Grotesk', system-ui, sans-serif;
  --font-head: 'Big Shoulders Display', sans-serif;
}

/* midnight/ivory/leaf/edition blocks: same variable names, new values —
   copied from the corresponding [data-theme="..."] block in any .dc.html
   source file, filled in for real in Task 11 (theme switcher). Stub with
   just storm here so Tasks 6-10 have a working default to build against. */

* { box-sizing: border-box; }
body { margin: 0; background: var(--bg); color: var(--text); font-family: var(--font-sans); }
a { color: var(--accent); text-decoration: none; }
a:hover { color: var(--accent-hover); }

@keyframes pulse { 0%, 100% { opacity: 1; } 50% { opacity: .45; } }
@keyframes blink { 0%, 49% { opacity: 1; } 50%, 100% { opacity: 0; } }

.sleuth-rail { width: 272px; height: 100%; background: var(--panel-alt); border-right: 1px solid var(--border);
  display: flex; flex-direction: column; flex-shrink: 0; transition: width .32s cubic-bezier(.2,.7,.2,1); }
.rail-nav { display: flex; flex-direction: column; gap: 2px; padding: 6px 8px; }
.rail-nav .navitem { display: flex; align-items: center; padding: 10px; border-left: 2px solid transparent;
  color: var(--text-muted); font-size: 14px; }
.rail-nav .navitem.active { border-left-color: var(--accent); color: var(--text); font-weight: 600; }
.rail-nav .navitem:hover { background: var(--panel); }
.rail-account { margin-top: auto; border-top: 1px solid var(--border); padding: 10px; }

.app-shell { display: flex; height: 100vh; overflow: hidden; }
.app-content { flex: 1; overflow-y: auto; padding: 48px 56px; }

.card { background: var(--panel); border: 1px solid var(--border-strong); border-radius: 2px; }
.pill { display: inline-flex; align-items: center; gap: 6px; padding: 5px 12px; border-radius: 100px;
  font-size: 12px; font-weight: 600; }
.pill-ready { background: var(--status-ready-wash); color: var(--status-ready); }
.pill-indexing { background: var(--accent-wash); color: var(--accent-hover); }
.pill-failed { background: var(--status-neutral-wash); color: var(--status-neutral); opacity: 0.85; }
.dot { width: 6px; height: 6px; border-radius: 50%; }
.dot-pulse { animation: pulse 1.4s ease-in-out infinite; }

.btn-primary { padding: 12px 22px; background: var(--accent); color: var(--accent-on); border-radius: 2px;
  font-weight: 600; font-size: 13.5px; cursor: pointer; border: none; white-space: nowrap; }
.btn-primary:hover { background: var(--accent-hover); }
.input-mono { flex: 1; padding: 12px 16px; background: var(--panel); border: 1px solid var(--border-strong);
  border-radius: 2px; font-family: var(--font-mono); font-size: 13.5px; color: var(--text); outline: none; }
.input-mono:focus { border-color: var(--accent); }
```

- [ ] **Step 4: Write `web/src/api.js`**

```javascript
const BASE_URL = import.meta.env.VITE_API_URL || 'http://localhost:8000';

export function apiUrl(path) {
  return `${BASE_URL}${path}`;
}

export async function apiGet(path) {
  const res = await fetch(apiUrl(path));
  if (!res.ok) throw new Error(`GET ${path} failed: ${res.status}`);
  return res.json();
}

export async function apiPost(path, body) {
  const res = await fetch(apiUrl(path), {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  if (!res.ok) {
    const detail = await res.json().catch(() => ({}));
    const err = new Error(detail.detail || `POST ${path} failed: ${res.status}`);
    err.status = res.status;
    throw err;
  }
  return res.json();
}
```

- [ ] **Step 5: Write `web/src/components/NavRail.jsx`**

Ported from `Sleuth Rail.dc.html` (see Design System section) — collapsible
272px↔72px rail with logo, repo switcher (GH badge hardcoded, no
multi-provider abbreviation logic since GitHub is the only provider), nav
items (Chat/Repos/Indexing status/Settings — **no Eval item**, eval stays
CLI-only), and an account menu (theme swatches wired up in Task 11, log out
wired here via `logout()`).

```jsx
import { NavLink } from 'react-router-dom';
import { logout } from '../api';

const NAV_ITEMS = [
  { to: '/app/chat', label: 'Chat' },
  { to: '/app/repos', label: 'Repos' },
  { to: '/app/indexing', label: 'Indexing status' },
  { to: '/app/settings', label: 'Settings' },
];

export default function NavRail() {
  return (
    <nav className="sleuth-rail">
      <NavLink to="/app/repos" className="rail-logo" title="Sleuth dashboard">
        <span className="logo-mark" />
      </NavLink>
      <div className="rail-nav">
        {NAV_ITEMS.map((item) => (
          <NavLink key={item.to} to={item.to} className={({ isActive }) => (isActive ? 'navitem active' : 'navitem')}>
            {item.label}
          </NavLink>
        ))}
      </div>
      <div className="rail-account">
        <button type="button" onClick={() => logout().then(() => window.location.assign('/login'))}>
          Log out
        </button>
      </div>
    </nav>
  );
}
```

- [ ] **Step 6: Write `web/src/components/AppShell.jsx`**

```jsx
import { Outlet } from 'react-router-dom';
import NavRail from './NavRail';

export default function AppShell() {
  return (
    <div className="app-shell">
      <NavRail />
      <div className="app-content">
        <Outlet />
      </div>
    </div>
  );
}
```

- [ ] **Step 7: Write `web/src/App.jsx`** (screens are stubs until Tasks 6-11 fill them in;
`RequireAuth` itself is added in Task 6 alongside `LoginPage` — sketch the shape here so
routing compiles, replace the inline stub with the real thing in Task 6)

```jsx
import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom';
import AppShell from './components/AppShell';
import LandingPage from './components/LandingPage';
import RepoList from './components/RepoList';
import IndexingScreen from './components/IndexingScreen';
import ChatScreen from './components/ChatScreen';
import RepoSettingsScreen from './components/RepoSettingsScreen';
import './theme.css';

export default function App() {
  return (
    <BrowserRouter>
      <Routes>
        <Route path="/" element={<LandingPage />} />
        {/* /login route added in Task 6 */}
        <Route path="/app" element={<AppShell />}>
          <Route index element={<Navigate to="repos" replace />} />
          <Route path="repos" element={<RepoList />} />
          <Route path="indexing/:repoId?" element={<IndexingScreen />} />
          <Route path="chat/:repoId?" element={<ChatScreen />} />
          <Route path="settings/:repoId?" element={<RepoSettingsScreen />} />
        </Route>
      </Routes>
    </BrowserRouter>
  );
}
```

- [ ] **Step 8: Add design fonts to `web/index.html`**

Per the Design System section — `Big Shoulders Display` (headers, Type A
only), `Space Grotesk` (body/UI), `JetBrains Mono` (data/labels/code):

```html
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Big+Shoulders+Display:wght@400;600;700;800;900&family=Space+Grotesk:wght@300;400;500;600;700&family=JetBrains+Mono:wght@400;500;600&display=swap" rel="stylesheet">
```

- [ ] **Step 9: Manual test**

Run: `cd web && npm run dev`
Expected: blank dark page at `/`, navigating to `/app/repos` shows the rail with no crash (screens are stubs — filled in next tasks; auth isn't wired until Task 6 so this is a pre-auth sanity check only).

- [ ] **Step 10: Commit**

```bash
git add web
git commit -m "feat: scaffold Vite React app with design tokens and routing shell"
```

---

## Task 6: Login screen + auth-aware routing

**Files:**
- Create: `web/src/components/LoginPage.jsx`
- Modify: `web/src/App.jsx` (add `/login` route, `RequireAuth` wrapper around
  the authenticated routes, redirect-to-login-on-401 handling)
- Modify: `web/src/api.js` (add `getMe`, `updateMe`, all `fetch` calls get
  `credentials: 'include'` so the session cookie is sent)

**Interfaces:**
- Consumes: Task 0's `GET /auth/github`, `GET /me`, `POST /auth/email`,
  `POST /auth/logout`.
- Produces: `RequireAuth` wrapper used by every screen task from here on
  (Task 7's `AppShell` sits inside it).

- [ ] **Step 1: Add `credentials: 'include'` + auth calls to `web/src/api.js`**

Every `apiGet`/`apiPost` helper's `fetch()` call gains
`credentials: 'include'` so the browser sends the `sleuth_session` cookie
cross-origin (dev: `localhost:5173` → `localhost:8000`). Add:

```javascript
export function getMe() {
  return apiGet('/me');
}

export function updateMe(patch) {
  return apiPatch('/me', patch);
}

export function requestMagicLink(email) {
  return apiPost('/auth/email', { email });
}

export function githubLoginUrl() {
  return `${API_BASE}/auth/github`;
}

export function logout() {
  return apiPost('/auth/logout', {});
}
```

- [ ] **Step 2: Write `web/src/components/LoginPage.jsx`**

Ported from `Sleuth Login.dc.html`: a GitHub button (plain `<a href={githubLoginUrl()}>`,
a full page navigation since OAuth is a redirect flow, not a fetch) and an
email form that calls `requestMagicLink` and shows "check your email" on
success. GitLab/Bitbucket buttons from the source design are dropped
entirely — GitHub-only per project decision.

```jsx
import { useState } from 'react';
import { githubLoginUrl, requestMagicLink } from '../api';

export default function LoginPage() {
  const [email, setEmail] = useState('');
  const [sent, setSent] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);

  async function handleEmailSubmit(e) {
    e.preventDefault();
    if (!email.trim() || busy) return;
    setBusy(true);
    setError(null);
    try {
      await requestMagicLink(email.trim());
      setSent(true);
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="login-shell">
      <div className="login-card">
        <h1>Log in to Sleuth</h1>
        {error && <div className="login-error">{error}</div>}
        <a className="oauth-btn" href={githubLoginUrl()}>
          Continue with GitHub
        </a>
        <div className="login-divider">or continue with email</div>
        <form onSubmit={handleEmailSubmit}>
          <input
            type="email"
            required
            value={email}
            disabled={busy}
            onChange={(e) => setEmail(e.target.value)}
            placeholder="you@company.com"
          />
          <button type="submit" disabled={busy || !email.trim()}>
            {busy ? 'Sending…' : 'Send magic link'}
          </button>
          {sent && <div className="login-sent">Check your email for a link to log in.</div>}
        </form>
      </div>
    </div>
  );
}
```

- [ ] **Step 3: Add `RequireAuth` + `/login` route to `web/src/App.jsx`**

```jsx
function RequireAuth({ children }) {
  const [status, setStatus] = useState('loading'); // loading | ok | unauth
  useEffect(() => {
    getMe().then(() => setStatus('ok')).catch(() => setStatus('unauth'));
  }, []);
  if (status === 'loading') return null;
  if (status === 'unauth') return <Navigate to="/login" replace />;
  return children;
}
```

Wrap the `AppShell`-rooted routes (Dashboard/Indexing/Chat/Settings) in
`RequireAuth`; `/`, `/login`, and the auth callback redirects stay outside it.

- [ ] **Step 4: Manual test**

Visit the app logged out, confirm redirect to `/login`. Click "Continue with
GitHub", complete the OAuth flow against a real registered GitHub OAuth App,
confirm redirect back into the app and `GET /me` succeeds. Log out, confirm
redirect back to `/login`. Request a magic link against a local SMTP catcher
(Mailpit/MailHog), open the link, confirm login succeeds.

- [ ] **Step 5: Commit**

```bash
git add web/src/components/LoginPage.jsx web/src/App.jsx web/src/api.js
git commit -m "feat: add Login screen and auth-aware routing"
```

---

## Task 7: Landing page

**Files:**
- Create: `web/src/components/LandingPage.jsx`

**Interfaces:**
- Consumes: `theme.css` tokens only. No backend calls — this is the marketing
  page from `Sleuth Landing.dc.html`, not live data.
- Produces: nothing consumed by later tasks.

- [ ] **Step 1: Write `web/src/components/LandingPage.jsx`**

Translate `Sleuth Landing.dc.html` section-by-section into JSX, matching the
source's copy, structure, and animation beats exactly (this is a from-scratch
rewrite against the *new* design source — do not reuse copy/section-order
from any earlier draft of this task):
- Header: logo mark + "SLEUTH" wordmark, nav links (`#problem` "Why it's
  hard", `#features` "Features", `#how` "How it works", `#preview`
  "Product"), "Connect a repo" pill button → `/login` (not a raw anchor —
  connecting a repo requires being logged in, so route unauthenticated
  visitors through login first; Task 6 delivers `RequireAuth`, so this link
  just goes to `/login` and post-login routing lands them on `/app/repos`).
- Hero (`section` no id): eyebrow "Code intelligence, grounded", headline
  "Understand any codebase without reading every file.", subhead paragraph,
  two CTAs ("Connect a repo" → `/login`, "See how it works" → `#how`), and
  the four-tag strip (AST-LEVEL INDEXING / TOOL-LOOP RETRIEVAL / FILE:LINE
  CITATIONS / REPO-SCOPED CONTEXT). Animated SVG line/node background is a
  decorative `<svg>` — port as static JSX (the CSS `@keyframes drift1/drift2/
  pulseNode/traverse` in `theme.css` handle the animation, no JS needed).
- `#problem` section ("01 / THE PROBLEM"): "Text chunking breaks code."
  headline + 3-row numbered list (functions cut mid-body / call
  relationships disappear / "what calls this?" is unanswerable) — exact
  copy from the source.
- `#features` section ("02 / WHAT SLEUTH DOES"): "Built for the shape of a
  repository." + 3 cards (STRUCTURE-AWARE CHUNKING / AGENTIC RETRIEVAL /
  REPO-SCOPED & CITED), each with a monospace code-like detail block —
  exact copy from the source.
- `#how` section ("03 / HOW IT WORKS"): "Three steps, then ask anything." +
  3-column numbered steps (Connect a repo / Sleuth indexes structurally /
  Ask, get cited answers). Step 1's copy in the source says "Point Sleuth
  at GitHub, GitLab or a local clone" — **correct this to GitHub-only** when
  porting, since GitLab/Bitbucket support was dropped project-wide.
- `#preview` section: "Answers you can check in one click." + a static
  browser-chrome mock showing a real Q&A exchange (refresh-token
  invalidation example) with a code citation block — reproduce as static
  JSX, no live data, no autotyping (the source's underlying animation here
  is just the `data-reveal` fade-in, not a typing effect — simpler than the
  old draft of this task assumed).
- `#cta` section: "Stop guessing. Start reading the code." + "Connect a
  repo" button → `/login`.
- Footer: wordmark, copyright, 4 anchor links (Product/Docs/Security/Contact
  → `#features`/`#how`/`#problem`/`#cta`).

Use `IntersectionObserver` for the fade/slide-in-on-scroll behavior on every
`data-reveal` section, matching the source's `componentDidMount` logic
(unobserve after first reveal, 2.6s fallback timeout in case the observer
never fires).

- [ ] **Step 2: Manual test**

Run: `npm run dev`, open `/`.
Expected: sections fade/slide in as you scroll past them, all nav anchors
scroll to the right section, both "Connect a repo" CTAs route to `/login`.

- [ ] **Step 3: Commit**

```bash
git add web/src/components/LandingPage.jsx
git commit -m "feat: add landing page"
```

---

## Task 8: Repos screen (real data)

**Files:**
- Create: `web/src/components/RepoList.jsx`, `web/src/components/AddRepoForm.jsx`, `web/src/components/RepoStatusBadge.jsx`
- Modify: `web/src/api.js` (add `listRepos`, `addRepo`)

**Interfaces:**
- Consumes: Task 1's `GET /repos`, `POST /repos` (via `web/src/api.js`).
- Produces: nothing consumed by later tasks (Chat screen fetches repos independently via the same `listRepos` helper).

- [ ] **Step 1: Add repo calls to `web/src/api.js`**

```javascript
export function listRepos() {
  return apiGet('/repos');
}

export function addRepo(githubUrl) {
  return apiPost('/repos', { github_url: githubUrl });
}
```

- [ ] **Step 2: Write `web/src/components/RepoStatusBadge.jsx`**

```jsx
export default function RepoStatusBadge({ status }) {
  const cls = status === 'ready' ? 'pill pill-ready' : status === 'indexing' ? 'pill pill-indexing' : status === 'failed' ? 'pill pill-failed' : 'pill pill-indexing';
  const dotCls = status === 'indexing' ? 'dot dot-pulse' : 'dot';
  return (
    <div className={cls}>
      <span className={dotCls} style={{ background: 'currentColor' }} />
      {status}
    </div>
  );
}
```

- [ ] **Step 3: Write `web/src/components/AddRepoForm.jsx`**

```jsx
import { useState } from 'react';
import { addRepo } from '../api';

export default function AddRepoForm({ onAdded }) {
  const [url, setUrl] = useState('');
  const [error, setError] = useState(null);

  async function handleAdd() {
    if (!url.trim()) return;
    try {
      const repo = await addRepo(url.trim());
      setUrl('');
      setError(null);
      onAdded(repo);
    } catch (err) {
      setError(err.message);
    }
  }

  return (
    <div style={{ marginBottom: 24 }}>
      <div style={{ display: 'flex', gap: 10 }}>
        <input
          className="input-mono"
          placeholder="github.com/owner/repo"
          value={url}
          onChange={(e) => setUrl(e.target.value)}
          onKeyDown={(e) => e.key === 'Enter' && handleAdd()}
        />
        <button className="btn-primary" onClick={handleAdd}>Index repo</button>
      </div>
      {error && <div style={{ color: 'var(--status-neutral)', fontSize: 12.5, marginTop: 8 }}>{error}</div>}
    </div>
  );
}
```

- [ ] **Step 4: Write `web/src/components/RepoList.jsx`**

```jsx
import { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { listRepos } from '../api';
import AddRepoForm from './AddRepoForm';
import RepoStatusBadge from './RepoStatusBadge';

function repoDetail(repo) {
  if (repo.status === 'failed') return repo.error_message || 'indexing failed';
  if (repo.status === 'ready') return `${repo.embedding_model || 'voyage-code-3'} · ready`;
  return 'indexing…';
}

export default function RepoList() {
  const [repos, setRepos] = useState([]);

  async function refresh() {
    setRepos(await listRepos());
  }

  useEffect(() => {
    refresh();
    const interval = setInterval(refresh, 3000);
    return () => clearInterval(interval);
  }, []);

  return (
    <div style={{ maxWidth: 920, margin: '0 auto' }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', marginBottom: 8 }}>
        <h1 style={{ fontSize: 28, fontWeight: 600, margin: 0 }}>Repos</h1>
        <span style={{ fontFamily: 'var(--font-mono)', fontSize: 12.5, color: 'var(--text-faint)' }}>
          {repos.filter((r) => r.status === 'ready').length} indexed
        </span>
      </div>
      <p style={{ fontSize: 14, color: 'var(--text-muted)', marginBottom: 28 }}>
        Point Sleuth at a GitHub URL. It clones, parses, and indexes it in the background.
      </p>

      <AddRepoForm onAdded={(repo) => setRepos((prev) => [repo, ...prev])} />

      <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
        {repos.map((repo) => {
          const name = repo.github_url.replace(/^https?:\/\/github\.com\//, '');
          return (
            <div key={repo.id} className="card" style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '18px 20px' }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 14 }}>
                <div style={{ width: 36, height: 36, borderRadius: 8, background: 'oklch(1 0 0 / 0.05)', display: 'flex', alignItems: 'center', justifyContent: 'center', fontFamily: 'var(--font-mono)', color: 'var(--text-muted)' }}>
                  {name.charAt(0).toUpperCase()}
                </div>
                <div>
                  <div style={{ fontFamily: 'var(--font-mono)', fontSize: 14.5, fontWeight: 500, marginBottom: 4 }}>{name}</div>
                  <div style={{ fontSize: 12.5, color: 'var(--text-faint)' }}>{repoDetail(repo)}</div>
                </div>
              </div>
              <div style={{ display: 'flex', alignItems: 'center', gap: 16 }}>
                <RepoStatusBadge status={repo.status} />
                {repo.status !== 'ready' && (
                  <Link to={`/app/indexing/${repo.id}`} style={{ fontSize: 12, color: 'var(--accent)' }}>watch →</Link>
                )}
                {repo.status === 'ready' && (
                  <Link to={`/app/chat/${repo.id}`} style={{ fontSize: 12, color: 'var(--accent)' }}>chat →</Link>
                )}
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}
```

- [ ] **Step 5: Manual test**

Run: `uvicorn sleuth.api.main:app --reload` (backend) + `npm run dev` (frontend), add a
real small repo, confirm it appears immediately as `pending`/`indexing`, and flips to
`ready` (or `failed`) within the poll interval without a page reload.

- [ ] **Step 6: Commit**

```bash
git add web/src/components/RepoList.jsx web/src/components/AddRepoForm.jsx web/src/components/RepoStatusBadge.jsx web/src/api.js
git commit -m "feat: add Repos screen wired to the real API"
```

---

## Task 9: Indexing screen (real data)

**Files:**
- Create: `web/src/components/IndexingScreen.jsx`
- Modify: `web/src/api.js` (add `getRepo`, `getProgress`)

**Interfaces:**
- Consumes: Task 2's `GET /repos/{id}/progress`, Task 1's `GET /repos/{id}`.

- [ ] **Step 1: Add calls to `web/src/api.js`**

```javascript
export function getRepo(repoId) {
  return apiGet(`/repos/${repoId}`);
}

export function getProgress(repoId) {
  return apiGet(`/repos/${repoId}/progress`);
}
```

- [ ] **Step 2: Write `web/src/components/IndexingScreen.jsx`**

```jsx
import { useEffect, useState } from 'react';
import { useParams } from 'react-router-dom';
import { getProgress, getRepo } from '../api';

const STEP_ORDER = ['cloning', 'cloned', 'parsed', 'chunked', 'embedding_start', 'embedding_progress', 'stored', 'ready'];
const STEP_LABELS = { cloning: 'Clone', cloned: 'Clone', parsed: 'Parse', chunked: 'Chunk', embedding_start: 'Embed', embedding_progress: 'Embed', stored: 'Store', ready: 'Store' };
const DISPLAY_STEPS = ['Clone', 'Parse', 'Chunk', 'Embed', 'Store'];

function stepIndex(step) {
  const label = STEP_LABELS[step] || 'Clone';
  return DISPLAY_STEPS.indexOf(label);
}

export default function IndexingScreen() {
  const { repoId } = useParams();
  const [repo, setRepo] = useState(null);
  const [progress, setProgress] = useState(null);

  useEffect(() => {
    if (!repoId) return;
    let cancelled = false;
    async function poll() {
      const [r, p] = await Promise.all([getRepo(repoId), getProgress(repoId)]);
      if (!cancelled) {
        setRepo(r);
        setProgress(p);
      }
    }
    poll();
    const interval = setInterval(poll, 1500);
    return () => {
      cancelled = true;
      clearInterval(interval);
    };
  }, [repoId]);

  if (!repoId) return <p style={{ color: 'var(--text-muted)' }}>Select a repo from the Repos screen to watch its indexing progress.</p>;
  if (!repo || !progress) return <p style={{ color: 'var(--text-muted)' }}>Loading…</p>;

  const activeIdx = stepIndex(progress.step);
  const detail = progress.detail || {};

  return (
    <div style={{ maxWidth: 980, margin: '0 auto' }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', marginBottom: 6 }}>
        <h1 style={{ fontSize: 28, fontWeight: 600, margin: 0 }}>Indexing</h1>
        <span style={{ fontFamily: 'var(--font-mono)', fontSize: 12.5, color: 'var(--accent-hover)' }}>
          {repo.github_url.replace(/^https?:\/\/github\.com\//, '')}
        </span>
      </div>
      <p style={{ fontSize: 14, color: 'var(--text-muted)', marginBottom: 32 }}>
        Incremental re-index via content_hash — unchanged chunks reuse their stored embedding.
      </p>

      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(5, 1fr)', position: 'relative', marginBottom: 40 }}>
        <div style={{ position: 'absolute', top: 19, left: '6%', right: '6%', height: 1, background: 'var(--border-strong)', zIndex: 0 }} />
        {DISPLAY_STEPS.map((label, i) => {
          const done = i < activeIdx || repo.status === 'ready';
          const active = i === activeIdx && repo.status !== 'ready';
          return (
            <div key={label} style={{ display: 'flex', flexDirection: 'column', gap: 14, position: 'relative', zIndex: 1 }}>
              <div
                className={active ? 'dot-pulse' : ''}
                style={{
                  width: 38, height: 38, borderRadius: '50%', display: 'flex', alignItems: 'center', justifyContent: 'center',
                  fontFamily: 'var(--font-mono)', fontSize: 13,
                  background: done ? 'var(--status-ready-wash)' : 'var(--bg)',
                  border: `1.5px solid ${done ? 'var(--status-ready)' : active ? 'var(--accent)' : 'var(--border-strong)'}`,
                  color: done ? 'var(--status-ready)' : active ? 'var(--accent)' : 'var(--text-faint)',
                }}
              >
                {done ? '✓' : i + 1}
              </div>
              <div style={{ fontWeight: 600, fontSize: 14.5, color: active ? 'var(--text)' : done ? 'var(--text-secondary)' : 'var(--text-faint)' }}>{label}</div>
            </div>
          );
        })}
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: '1.3fr 1fr', gap: 24 }}>
        <div className="card">
          <div style={{ padding: '14px 20px', borderBottom: '1px solid var(--border)', fontFamily: 'var(--font-mono)', fontSize: 12, color: 'var(--text-faint)' }}>live log</div>
          <div style={{ padding: 20, fontFamily: 'var(--font-mono)', fontSize: 12.5, lineHeight: 2, height: 260, overflowY: 'auto', color: 'var(--text-muted)' }}>
            {progress.log.map((entry, i) => (
              <div key={i}>{entry.step} {JSON.stringify(Object.fromEntries(Object.entries(entry).filter(([k]) => k !== 'step')))}</div>
            ))}
          </div>
        </div>

        <div className="card" style={{ padding: 26, display: 'flex', flexDirection: 'column', gap: 16 }}>
          <Stat label="Files scanned" value={detail.files ?? '—'} />
          <Stat label="Chunks created" value={detail.chunks ?? '—'} />
          <Stat label="Skipped (unchanged)" value={detail.skipped_unchanged ?? '—'} accent />
          <Stat label="Embedding model" value={repo.embedding_model || 'voyage-code-3'} />
          <Stat label="Elapsed" value={`${Math.round(progress.elapsed_seconds)}s`} />
        </div>
      </div>
    </div>
  );
}

function Stat({ label, value, accent }) {
  return (
    <div style={{ display: 'flex', justifyContent: 'space-between' }}>
      <span style={{ fontSize: 13, color: 'var(--text-muted)' }}>{label}</span>
      <span style={{ fontFamily: 'var(--font-mono)', fontSize: 13, color: accent ? 'var(--status-ready)' : 'var(--text)' }}>{value}</span>
    </div>
  );
}
```

- [ ] **Step 3: Manual test**

Add a repo from the Repos screen, click "watch →", confirm the step tracker advances
in near-real-time, the live log grows, and elapsed time ticks up; confirm it settles
on "Store" done once the repo flips to `ready`.

- [ ] **Step 4: Commit**

```bash
git add web/src/components/IndexingScreen.jsx web/src/api.js
git commit -m "feat: add Indexing screen wired to live progress"
```

---

## Task 10: Chat screen (real data)

**Files:**
- Create: `web/src/components/ChatScreen.jsx`, `web/src/components/ChatSidebar.jsx`, `web/src/components/MessageList.jsx`, `web/src/components/Composer.jsx`
- Modify: `web/src/api.js` (add `listChats`, `createChat`, `getMessages`, `streamChat`)

**Interfaces:**
- Consumes: Task 3's chat CRUD endpoints, Task 4's `POST /chat` SSE endpoint, Task 1's `listRepos`.

- [ ] **Step 1: Add calls to `web/src/api.js`**

```javascript
export function listChats(repoId) {
  return apiGet(`/chats?repo_id=${repoId}`);
}

export function createChat(repoId) {
  return apiPost('/chats', { repo_id: repoId });
}

export function getMessages(chatId) {
  return apiGet(`/chats/${chatId}/messages`);
}

export async function streamChat(chatId, question, { onSources, onToken, onDone }) {
  const res = await fetch(apiUrl('/chat'), {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ chat_id: chatId, question }),
  });
  if (!res.ok) throw new Error(`chat failed: ${res.status}`);

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });

    let frameEnd;
    while ((frameEnd = buffer.indexOf('\n\n')) !== -1) {
      const frame = buffer.slice(0, frameEnd);
      buffer = buffer.slice(frameEnd + 2);
      const lines = frame.split('\n');
      const eventLine = lines.find((l) => l.startsWith('event: '));
      const dataLine = lines.find((l) => l.startsWith('data: '));
      const eventType = eventLine ? eventLine.slice('event: '.length) : 'message';
      const data = dataLine ? dataLine.slice('data: '.length) : '';

      if (eventType === 'sources') onSources(JSON.parse(data));
      else if (eventType === 'done') onDone();
      else onToken(data);
    }
  }
}
```

- [ ] **Step 2: Write `web/src/components/MessageList.jsx`**

```jsx
export default function MessageList({ messages, streamingText, thinking }) {
  return (
    <div style={{ flex: 1, overflowY: 'auto', padding: 32, display: 'flex', flexDirection: 'column', gap: 22 }}>
      {messages.map((m) => (
        <MessageRow key={m.id} role={m.role} text={m.content} sources={m.sources} />
      ))}
      {thinking && <MessageRow role="assistant" text="" thinking />}
      {streamingText !== null && <MessageRow role="assistant" text={streamingText} streaming />}
    </div>
  );
}

function MessageRow({ role, text, sources, thinking, streaming }) {
  const isUser = role === 'user';
  return (
    <div style={{ alignSelf: isUser ? 'flex-end' : 'flex-start', maxWidth: isUser ? '70%' : '78%' }}>
      <div style={{ display: 'flex', gap: 10, flexDirection: isUser ? 'row-reverse' : 'row' }}>
        <div style={{
          width: 26, height: 26, borderRadius: '50%', flexShrink: 0, background: 'var(--accent-wash)',
          border: '1px solid oklch(0.62 0.10 148 / 0.5)', display: 'flex', alignItems: 'center', justifyContent: 'center',
          fontFamily: 'var(--font-mono)', fontSize: 10.5, color: 'var(--accent)',
        }}>
          {isUser ? 'Y' : 'S'}
        </div>
        <div style={isUser
          ? { background: 'var(--accent-wash)', border: '1px solid oklch(0.62 0.10 148 / 0.3)', padding: '12px 16px', borderRadius: '12px 12px 2px 12px', fontSize: 14 }
          : { background: 'var(--panel)', border: '1px solid var(--border)', padding: '14px 18px', borderRadius: '2px 14px 14px 14px', fontSize: 14, lineHeight: 1.65 }
        }>
          {thinking ? <span style={{ color: 'var(--text-muted)' }}>Thinking…</span> : text}
          {streaming && <span style={{ animation: 'blink 1s step-start infinite' }}>▊</span>}
        </div>
      </div>
      {sources && sources.length > 0 && (
        <div style={{ marginTop: 10, marginLeft: 36 }}>
          <div style={{ fontFamily: 'var(--font-mono)', fontSize: 10.5, color: 'var(--text-faint)', marginBottom: 6 }}>SOURCES</div>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
            {sources.map((s, i) => (
              <div key={i} style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '8px 12px', background: 'oklch(1 0 0 / 0.035)', border: '1px solid var(--border)', borderRadius: 8, maxWidth: 520 }}>
                <span style={{ width: 5, height: 5, background: 'var(--accent)' }} />
                <span style={{ fontFamily: 'var(--font-mono)', fontSize: 12, color: 'var(--text-secondary)', flex: 1, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{s.file_path}</span>
                <span style={{ fontFamily: 'var(--font-mono)', fontSize: 11, color: 'var(--text-faint)', whiteSpace: 'nowrap' }}>L{s.start_line}–{s.end_line}</span>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
```

- [ ] **Step 3: Write `web/src/components/Composer.jsx`**

```jsx
import { useState } from 'react';

export default function Composer({ onSend, disabled, modelName }) {
  const [draft, setDraft] = useState('');
  const canSend = draft.trim().length > 0 && !disabled;

  function handleSend() {
    if (!canSend) return;
    onSend(draft.trim());
    setDraft('');
  }

  return (
    <div style={{ padding: '16px 28px 26px' }}>
      <div style={{ background: 'var(--panel-alt)', border: '1px solid var(--border-strong)', borderRadius: 18, padding: '6px 6px 10px' }}>
        <textarea
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); handleSend(); } }}
          placeholder="Ask about this repo…"
          rows={2}
          style={{ width: '100%', background: 'transparent', border: 'none', outline: 'none', resize: 'none', color: 'var(--text)', fontSize: 14.5, lineHeight: 1.55, fontFamily: 'inherit', padding: '10px 12px 6px' }}
        />
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '8px 10px 0', borderTop: '1px solid var(--border)', marginTop: 2 }}>
          <span style={{ fontFamily: 'var(--font-mono)', fontSize: 10.5, padding: '4px 10px', borderRadius: 6, background: 'var(--accent-wash)', color: 'var(--accent)' }}>{modelName}</span>
          <button
            onClick={handleSend}
            disabled={!canSend}
            style={{
              width: 36, height: 36, borderRadius: '50%', border: 'none', cursor: canSend ? 'pointer' : 'default',
              background: canSend ? 'var(--accent)' : 'oklch(1 0 0 / 0.06)', display: 'flex', alignItems: 'center', justifyContent: 'center',
            }}
          >
            ↑
          </button>
        </div>
      </div>
    </div>
  );
}
```

- [ ] **Step 4: Write `web/src/components/ChatSidebar.jsx`**

```jsx
export default function ChatSidebar({ repos, activeRepoId, onSelectRepo, chats, activeChatId, onSelectChat, onNewChat }) {
  return (
    <div style={{ width: 270, borderRight: '1px solid var(--border)', flexShrink: 0, display: 'flex', flexDirection: 'column', background: 'var(--panel)' }}>
      <div style={{ padding: '18px 16px 14px', borderBottom: '1px solid var(--border)' }}>
        <div style={{ fontFamily: 'var(--font-mono)', fontSize: 10.5, color: 'var(--text-faint)', marginBottom: 10 }}>REPO</div>
        <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
          {repos.map((r) => (
            <div
              key={r.id}
              onClick={() => onSelectRepo(r.id)}
              style={{
                padding: '6px 12px', borderRadius: 100, fontFamily: 'var(--font-mono)', fontSize: 11.5, cursor: 'pointer',
                background: r.id === activeRepoId ? 'var(--accent-wash)' : 'oklch(1 0 0 / 0.05)',
                color: r.id === activeRepoId ? 'var(--accent)' : 'var(--text-muted)',
              }}
            >
              {r.github_url.replace(/^https?:\/\/github\.com\//, '')}
            </div>
          ))}
        </div>
      </div>
      <div style={{ flex: 1, overflowY: 'auto', padding: 14 }}>
        <div onClick={onNewChat} style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '11px 12px', borderRadius: 10, fontSize: 13, cursor: 'pointer', marginBottom: 16, border: '1px dashed oklch(0.62 0.10 148 / 0.45)', color: 'var(--accent)' }}>
          + New chat
        </div>
        <div style={{ fontFamily: 'var(--font-mono)', fontSize: 10.5, color: 'var(--text-faint)', padding: '0 4px', marginBottom: 8 }}>CHATS</div>
        {chats.map((c) => (
          <div
            key={c.id}
            onClick={() => onSelectChat(c.id)}
            style={{
              padding: '11px 12px 11px 14px', borderRadius: 8, cursor: 'pointer', marginBottom: 4,
              borderLeft: `2px solid ${c.id === activeChatId ? 'var(--accent)' : 'transparent'}`,
              background: c.id === activeChatId ? 'oklch(1 0 0 / 0.06)' : 'transparent',
            }}
          >
            <div style={{ fontSize: 13, color: c.id === activeChatId ? 'var(--text)' : 'var(--text-secondary)' }}>{c.title}</div>
            <div style={{ fontSize: 11.5, color: 'var(--text-faint)', marginTop: 2 }}>{c.message_count} messages</div>
          </div>
        ))}
      </div>
    </div>
  );
}
```

- [ ] **Step 5: Write `web/src/components/ChatScreen.jsx`**

```jsx
import { useEffect, useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import { createChat, getMessages, listChats, listRepos, streamChat } from '../api';
import ChatSidebar from './ChatSidebar';
import Composer from './Composer';
import MessageList from './MessageList';

export default function ChatScreen() {
  const { repoId } = useParams();
  const navigate = useNavigate();
  const [repos, setRepos] = useState([]);
  const [chats, setChats] = useState([]);
  const [activeChatId, setActiveChatId] = useState(null);
  const [messages, setMessages] = useState([]);
  const [streamingText, setStreamingText] = useState(null);
  const [thinking, setThinking] = useState(false);
  const [pendingSources, setPendingSources] = useState(null);

  useEffect(() => {
    listRepos().then((all) => {
      const ready = all.filter((r) => r.status === 'ready');
      setRepos(ready);
      if (!repoId && ready.length > 0) navigate(`/app/chat/${ready[0].id}`, { replace: true });
    });
  }, []);

  useEffect(() => {
    if (!repoId) return;
    listChats(repoId).then((cs) => {
      setChats(cs);
      setActiveChatId(cs[0]?.id ?? null);
    });
  }, [repoId]);

  useEffect(() => {
    if (!activeChatId) { setMessages([]); return; }
    getMessages(activeChatId).then(setMessages);
  }, [activeChatId]);

  async function handleNewChat() {
    const chat = await createChat(repoId);
    setChats((prev) => [{ ...chat, message_count: 0 }, ...prev]);
    setActiveChatId(chat.id);
  }

  async function handleSend(question) {
    let chatId = activeChatId;
    if (!chatId) {
      const chat = await createChat(repoId);
      setChats((prev) => [{ ...chat, message_count: 0 }, ...prev]);
      setActiveChatId(chat.id);
      chatId = chat.id;
    }

    setMessages((prev) => [...prev, { id: `local-${Date.now()}`, role: 'user', content: question, sources: null }]);
    setThinking(true);
    setStreamingText(null);
    setPendingSources(null);

    let text = '';
    await streamChat(chatId, question, {
      onSources: (sources) => setPendingSources(sources),
      onToken: (token) => { setThinking(false); text += token; setStreamingText(text); },
      onDone: () => {
        setMessages((prev) => [...prev, { id: `local-${Date.now()}-a`, role: 'assistant', content: text, sources: pendingSources }]);
        setStreamingText(null);
        setThinking(false);
      },
    });
  }

  if (repos.length === 0) {
    return <p style={{ color: 'var(--text-muted)' }}>No indexed repos yet — add one from the Repos screen first.</p>;
  }

  const activeChat = chats.find((c) => c.id === activeChatId);

  return (
    <div style={{ display: 'flex', height: '100%', margin: '-48px -56px' }}>
      <ChatSidebar
        repos={repos}
        activeRepoId={repoId}
        onSelectRepo={(id) => navigate(`/app/chat/${id}`)}
        chats={chats}
        activeChatId={activeChatId}
        onSelectChat={setActiveChatId}
        onNewChat={handleNewChat}
      />
      <div style={{ flex: 1, display: 'flex', flexDirection: 'column' }}>
        <div style={{ padding: '16px 28px', borderBottom: '1px solid var(--border)', display: 'flex', alignItems: 'center', gap: 10 }}>
          <span className="dot" style={{ background: 'var(--status-ready)' }} />
          <span style={{ fontSize: 13.5 }}>{repos.find((r) => r.id === repoId)?.github_url.replace(/^https?:\/\/github\.com\//, '')}</span>
          <span style={{ fontSize: 12, color: 'var(--text-faint)' }}>/ {activeChat?.title || 'New chat'}</span>
        </div>
        <MessageList messages={messages} streamingText={streamingText} thinking={thinking} />
        <Composer onSend={handleSend} modelName="voyage-code-3" />
      </div>
    </div>
  );
}
```

- [ ] **Step 6: Manual test**

Add a repo, wait for `ready`, go to `/app/chat`, ask a question, confirm: thinking
indicator shows until the first token, tokens stream into the assistant bubble,
sources appear once streaming finishes, "New chat" starts a fresh thread, and
reloading the page still shows prior chats/messages (persistence).

- [ ] **Step 7: Commit**

```bash
git add web/src/components/ChatScreen.jsx web/src/components/ChatSidebar.jsx web/src/components/MessageList.jsx web/src/components/Composer.jsx web/src/api.js
git commit -m "feat: add Chat screen wired to SSE streaming and persisted history"
```

---

## Task 11: Theme switcher (account menu)

**Files:**
- Modify: `web/src/components/NavRail.jsx` (account menu theme swatches)
- Modify: `web/src/api.js` (`updateMe` already added in Task 6, wire it here)
- Modify: `web/src/theme.css` (all 5 `[data-theme="..."]` blocks, copied
  verbatim from the `.dc.html` source files per the Design System section)

**Interfaces:**
- Consumes: Task 6's `getMe`/`updateMe`, Task 0's `theme_preference` field.

- [ ] **Step 1: Copy all 5 theme blocks into `web/src/theme.css`**

`storm` (default), `midnight`, `edition` (dark); `ivory`, `leaf` (light) —
exact hex/rgba values from any `.dc.html` file's `[data-theme="..."]` CSS
custom property block (identical across all 9 source files). Root element
gets `data-theme={user.theme_preference}` set from React state, driving
which block's custom properties apply.

- [ ] **Step 2: Add the theme swatch row to the account menu in `NavRail.jsx`**

Five small pill buttons (Storm/Midnight/Ivory/Leaf/Edition), active one
highlighted, `onClick` calls `updateMe({ theme_preference: name })` then
updates local state immediately (optimistic) so the switch feels instant
rather than waiting on the round-trip.

- [ ] **Step 3: Manual test**

Switch each of the 5 themes, confirm the whole app (rail, cards, buttons)
re-themes live, confirm the choice survives a page reload (persisted via
`GET /me` on load).

- [ ] **Step 4: Commit**

```bash
git add web/src/components/NavRail.jsx web/src/theme.css
git commit -m "feat: add persisted theme switcher"
```

---

## Task 12: Polish — error states + README

**Files:**
- Modify: `web/src/components/RepoList.jsx` (surface `error_message` prominently for `failed`)
- Modify: `web/src/components/ChatSidebar.jsx` / `ChatScreen.jsx` (disable/tooltip repos that aren't ready — already filtered to `ready` only in Task 10, so this task adds an explicit tooltip on the picker rather than a silent absence)
- Create/Update: top-level `README.md`

**Interfaces:**
- Consumes: nothing new.

- [ ] **Step 1: Add failed-repo error banner to `RepoList.jsx`**

Extend the card's status column: when `repo.status === 'failed'`, render the
`error_message` inline (not just in the tooltip-less detail line already added in
Task 8) — e.g. a small red-tinted banner under the card row using
`var(--status-neutral)` text on a `var(--status-neutral-wash)` background, so a
failure reads as a distinct visual state, not just muted text.

- [ ] **Step 2: Deliberately index a bad URL**

Manual test: submit `https://github.com/does-not-exist/nope-12345` via Add Repo,
confirm the card settles on `failed` with the clone error message visible, not a
spinner stuck on "indexing…" forever.

- [ ] **Step 3: Write `README.md`**

```markdown
# Sleuth

RAG chatbot over GitHub repos. See `CLAUDE.md` for the full project context.

## Running locally

Backend (from repo root, with `.venv` activated and `.env` populated per `.env.example`):

    uvicorn sleuth.api.main:app --reload

Frontend:

    cd web
    cp .env.example .env
    npm install
    npm run dev

Open http://localhost:5173. The API runs on http://localhost:8000.

## CLI

    python -m sleuth add <github_url>
    python -m sleuth list
    python -m sleuth ask <repo_id> "<question>"
    python -m sleuth agentic <path> "<question>"
    python -m sleuth eval <golden_yaml_path>
```

- [ ] **Step 4: Commit**

```bash
git add web/src/components/RepoList.jsx README.md
git commit -m "feat: polish error states and add README"
```

---

---

## Self-Review Notes

- **Spec coverage:** the new 9-file design (Login/Landing/Connect Repo/
  Dashboard/Indexing Status/Chat/Repo Settings + shared Rail) maps to Tasks
  6-11; every route needed to back those screens is covered (auth, repos,
  progress, chat CRUD + SSE, theme preference). Repo Settings screen itself
  (branch switch, re-index trigger, disconnect) is deliberately left as a
  follow-on task, not detailed step-by-step here — same shape as the other
  screen tasks, add when picked up.
- **Known design-to-reality deltas** (decided with the user 2026-08-19 and
  2026-08-24, not silent downgrades): Indexing shows elapsed time not a
  fabricated ETA; GitLab/Bitbucket OAuth buttons dropped, GitHub-only;
  Type A/B font toggle not shipped (Type A hardcoded); the 5-theme color
  switcher *is* shipped (unlike the old design's accent-picker, which
  stayed authoring-only); chat history is persisted (schema addition)
  rather than kept ephemeral; **Eval has no web screen in this plan** —
  stays CLI-only (`sleuth eval`), a clean candidate for its own future plan
  now that the design doesn't include an Eval mockup to build against.
- **Auth is new scope** (2026-08-24) — the 2026-08-19 draft explicitly
  excluded it. Task 0 adds it as a prerequisite gate (GitHub OAuth + email
  magic link via AWS SES SMTP), not a multi-tenancy system: no `user_id`
  FK on repos/chats, single expected user, schema allows more later without
  a rework.
- **Backward compatibility:** `ingest_repo`, `embed_batch`, `stream_answer`
  all gain one optional keyword-only-by-convention parameter each,
  defaulting to `None` — every existing call site (CLI, existing tests) is
  unaffected. `sleuth/eval/runner.py` and `sleuth/cli.py`'s `eval` command
  are untouched by this plan (no `EvalSummary` return-type change needed,
  since no web Eval screen consumes it).
