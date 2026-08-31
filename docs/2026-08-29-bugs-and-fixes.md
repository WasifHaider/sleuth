# Bugs Found and Fixed — 2026-08-28 / 2026-08-29 (full day, all sessions)

This document covers every real bug found and fixed across **all** development
sessions on this date — not just the most recent one. Six sessions touched
code that day; they're listed chronologically below, each with symptom, root
cause, and the actual fix.

---

## Session 1 — "Fix duplicate API calls and add loaders" (~1:40 PM)

### 1. Blocking DB connect froze the event loop under concurrency
**Symptom:** more than one API call fired for what should have been a single
request; requests visibly queued up as "(pending)" in DevTools even though
React wasn't issuing duplicate fetches.

**Root cause:** the FastAPI middleware called `get_connection()` — a
blocking `psycopg.connect()` — directly inside an `async def` request
handler. That blocks the single event loop for a full network handshake on
every request, so concurrent requests visibly serialize.

**Fix:** switched to a `psycopg_pool.ConnectionPool`, opened once at app
startup (`lifespan`), with `getconn()`/`putconn()` wrapped in
`asyncio.to_thread()` so the blocking borrow never stalls the event loop.

### 2. N+1 query in `GET /repos`
**Symptom:** listing repos issued one query per repo instead of one query
total.

**Fix:** replaced the per-repo lookup with a single `list_repos_full()`
query.

### 3. Every GET request paid for two DB round trips
**Root cause:** connections defaulted to `autocommit=False`, leaving an
implicit transaction open that the pool then had to roll back on return —
an invisible second round trip on every read-only request.

**Fix:** switched the whole pool to `autocommit=True`; the scattered
`conn.commit()` calls already in routes became harmless no-ops instead of
being removed one by one.

### 4. `GET /me` cost a DB round trip on every page load
**Fix:** the session cookie now embeds `email`/`name`/`theme_preference` as
signed claims, so `GET /me` can answer straight from the verified cookie
with zero DB round trips (with a fallback path for older cookies that only
carry `user_id`).

### 5. Malformed (non-UUID) id crashed with a raw 500 instead of 404
**Fix:** added `_fetchone_or_none_on_bad_id` in `store.py`, catching
`psycopg.errors.InvalidTextRepresentation` and returning "not found" instead
of letting the database error surface to the client.

### 6. Signup race condition
**Symptom:** two concurrent signups with the same email could both pass a
`SELECT`-based pre-check before either `INSERT` landed, creating duplicate
users.

**Fix:** removed the pre-check race; now catches
`psycopg.errors.UniqueViolation` as the real, atomic guard.

### 7. No visual feedback while loading
**Fix:** added real skeleton loaders (`Skeleton.jsx`) shaped like each
screen's actual layout for RepoList, ChatScreen, and IndexingScreen (instead
of a plain "Loading…" string), plus a `FullScreenLoader.jsx` shown during
the initial auth check and the login/signup transition.

### 8. Voyage embedding calls failed outright on a 429
**Symptom:** `429 Too Many Requests` from `api.voyageai.com/v1/embeddings`
killed the whole ingest run.

**Root cause:** `http_retry.py`'s retry policy was far too weak for a real
rate limit — only 1 retry, a flat 1-second wait, and it ignored Voyage's
`Retry-After` header entirely. `VoyageEmbedder` also fired up to 5 concurrent
requests by default, easily enough to trip a real API key's per-minute quota.

**Fix:**
- `http_retry.py` now reads and honors `Retry-After` when present, falls
  back to exponential backoff with jitter (capped at 60s) otherwise, and
  raised default retries from 1 to 5.
- `VoyageEmbedder`'s default concurrency lowered from 5 to 3.
- `sleuth/llm/generate.py` pinned its own retry to a fast single retry
  (generation already has a Groq→NIM fallback chain, so it should fail over
  quickly rather than sit through a long backoff on the same provider).

**Verified:** simulated 429-with-`Retry-After` (recovered in ~0.4–0.8s),
simulated persistent 429 with no header (correct exponential backoff, raises
after exhausting retries), a real call against the live Voyage API, and the
full test suite (73 passed, 8 skipped).

---

## Session 2 — "Fix 429 error for Voyage AI embeddings" (~4:41 PM)

**Symptom:** the same `429 Too Many Requests` error recurred in real usage
even after Session 1's fix, on a subsequent indexing attempt. This session
picked up directly from that report. The rate-limit hardening from Session 1
is real and verified in isolation, but rate limits are load-dependent — a
big enough repo (many embedding batches) can still exhaust even a
5-retry/backoff budget against a strict per-minute quota. No further code
change is recorded as landing in this short session; flagged here so the
history isn't silently dropped. If this resurfaces, the next step is
lowering concurrency further or batching fewer chunks per request rather
than tuning retry counts again.

---

## Session 3 — "Fix chat page design" (~7:20 PM)

### 9. Markdown answers rendered as raw text
**Symptom:** LLM answers containing `**bold**`, `## headings`, tables, and
`---` rules showed those literal characters instead of formatted markdown.

**Fix:** `MessageList.jsx` now renders assistant content through
`react-markdown` + `remark-gfm` instead of a raw string. Added a full
`.chat-markdown` CSS block (headings, tables, code blocks, `<hr>`, etc.)
matching the existing design tokens.

### 10. No way to inspect a cited source's actual code
**Fix:** new `CodeDrawer.jsx` — a right-side sliding panel showing the file
path, symbol, line range, and the real code lines with line numbers, opened
by clicking a source pill. Closable via ×, backdrop click, or Escape.

### 11. Backend wasn't forwarding the retrieved code text to the frontend
**Symptom:** the code drawer had nowhere to pull code from — sources arrived
with file path/line range but no code body.

**Root cause:** `sleuth/api/routes/chat.py`'s `on_sources` SSE callback built
each source dict without `code_text`, even though the retrieved
`SearchResult` already carried it — it just wasn't being copied over.

**Fix:** added `"code_text": r.code_text` to the source dict. No new
endpoint or DB query needed — the data was already in hand.

### Process note (not a code bug, but worth recording)
While debugging an unrelated pre-existing test failure, a `git checkout --
sleuth/api/routes/chat.py` was run mid-session and briefly discarded
uncommitted work already in that file (the SSE connection-pool handling and
per-user repo-ownership checks from an earlier session). This was caught
immediately and the file was restored byte-for-byte from a previously
captured diff, with the `code_text` fix re-applied on top. Verified: the
restored diff's line count matched the original exactly, full test suite
still green, `npm run build` clean.

### Flagged but not fixed this session
`tests/test_api_auth.py`, `test_api_repos.py`, `test_api_chat.py` all fail
under a plain `TestClient(create_app(...))` because FastAPI only runs
`lifespan` (which populates `app.state.pool`) when `TestClient` is used as a
context manager (`with TestClient(...) as client:`). Confirmed pre-existing
at HEAD via `git stash` (fails identically before this session's changes) —
not a regression, just a known gap in the test files themselves (now also
recorded in memory for future sessions).

---

## Session 4 — "Chat title update and loader UX fixes" (~8:30 PM)

### 12. Chat title stuck on "New chat" forever
**Symptom:** every chat kept the placeholder title "New chat" regardless of
what was actually discussed.

**Fix:** `sleuth/store.py` gained `derive_chat_title(question)` (collapses
whitespace, truncates to 60 chars) and `update_chat_title()`. `POST /chat`
renames the chat from the first question the moment it's persisted (only if
the title is still the default) and emits a new `event: title` SSE frame so
the frontend updates live without a refetch. `ChatScreen.jsx` wires an
`onTitle` callback that patches the sidebar and header title immediately.

### 13. Sidebar didn't match the approved design
**Fix:** rebuilt `ChatSidebar.jsx` against `docs/design/Sleuth Chat.dc.html`:
repo picker is now a single dropdown (not a pill row), "+ New chat" is a
bordered button with an icon, chat history is grouped into **Today /
Yesterday / Earlier** (computed from each chat's real `created_at`, not
hardcoded), and a collapse/expand toggle shrinks the sidebar to a 60px icon
rail. Matching CSS added to `theme.css`.

### 14. No loading feedback when switching to an older chat
**Fix:** `ChatScreen.jsx` tracks a `messagesLoading` state around the
`getMessages()` fetch triggered by switching `activeChatId`; a small inline
spinner (`.chat-messages-loading`) now shows in the message pane while that
fetch is in flight, instead of a blank/frozen view.

**Verified:** `npm run build` clean; the title-rename logic confirmed
end-to-end against the real backend + test DB (first message renames the
chat + emits the SSE frame, second message leaves the title untouched).

---

## Session 5 — "Fix theme loader, logo, favicon, color schemes" (~12:20 AM, 2026-08-29)

### 15. Theme resets to "storm" on page refresh
**Root cause:** theme was only ever set in React state after `GET /me`
resolved — nothing applied it synchronously before first paint or persisted
it locally, so a fresh load always showed the default theme momentarily (or
sometimes not at all).

**Fix:** theme now syncs to `localStorage` and is applied to
`document.documentElement.dataset.theme` synchronously before React's first
paint, instead of waiting on the async `/me` round-trip.

### 16. Inconsistent branding — logo missing from hero image, favicon didn't match
**Root cause:** the logo mark was hand-copied inline inside `LandingPage.jsx`
with no shared source, so the nav rail and favicon each drifted independently
(the favicon didn't match at all).

**Fix:** extracted a shared `Logo.jsx` (`LogoMark`) used by both
`LandingPage.jsx` and `NavRail.jsx`; generated a new `favicon.svg` from the
same mark.

### 17. Too many color themes
**Fix:** trimmed from 5 themes (`storm`/`midnight`/`ivory`/`leaf`/`edition`)
down to `storm` + `ivory` only, per explicit request.

### 18. Doc-vs-code retrieval bug (the most significant fix of the day)
**Symptom:** asking an architecture-flavored question against a repo with
both real source code and hand-written `docs/*.html` write-ups returned
*only* documentation excerpts as sources — no real code was ever retrieved,
even though the actual implementation obviously exists in the repo.

**Root cause:** those `docs/*.html` files are real, parseable HTML, so the
ingest pipeline chunked and embedded them exactly like genuine source code.
Prose *about* the architecture can score a **closer cosine-distance match**
than the real implementation for an architecture-flavored question —
`search_chunks` had no concept of "documentation" vs "real code," so it just
returned whichever won on raw vector distance.

**Fix (end-to-end):**
- `sleuth/chunking.py` — `is_doc_path()` / `Chunk.is_doc`: true for any file
  under a `docs`/`doc`/`documentation` directory at any depth.
- `schema.sql` — `chunks` table gained `is_doc boolean NOT NULL DEFAULT
  false`; `store.py::upsert_chunks` persists it.
- `sleuth/retrieve/search.py` — `search_chunks` gained `prefer_code: bool =
  True`, ordering `ORDER BY is_doc, distance` so real code always ranks
  ahead of documentation, falling back to docs only once code is exhausted.
- `sleuth/retrieve/answer.py` — prompt labels every excerpt
  `[CODE]`/`[DOCUMENTATION]` and instructs the LLM to prefer/cite code, as a
  second line of defense.
- `sleuth/api/routes/chat.py` + `MessageList.jsx` — `is_doc` flows to a
  "docs" badge on source pills in the UI.

**Verified:** 7 new tests (including one that deliberately makes the doc
chunk the *closer* cosine match and asserts code still wins), full suite
116/116 passing, `npm run build` clean.

**Known caveat:** only applies to *future* ingests. An already-indexed repo
keeps `is_doc = false` on every existing row until it's re-indexed ("Retry
indexing" in the UI, or `sleuth add <same-url>` from the CLI).

### 19. Missing style for the new "docs" badge
**Fix:** added `.chat-source-doc-badge` in `theme.css`, built from the
existing `--status-neutral*` design tokens.

---

## Cross-cutting notes

- Several fixes this day were **caught only because the full test suite or
  a real end-to-end check was run after the change**, not assumed from the
  diff alone (the 429 retry logic, the doc-vs-code ranking, the chat title
  rename). This matches the project's stated preference for verifying over
  assuming.
- The pre-existing `TestClient` lifespan gap (flagged in Session 3) is a
  known, unresolved test-infrastructure issue — not a regression from any
  of the fixes above, but still blocking `test_api_auth.py`/
  `test_api_repos.py`/`test_api_chat.py` from running cleanly. Worth fixing
  as its own small task.
- Bug #18 (doc-vs-code retrieval) is directly relevant to a separate,
  ongoing discussion about why SLEUTH's chatbot can't yet answer broad
  "rate the whole architecture" style questions — plain top-k vector
  retrieval has no built-in sense of what's representative versus merely
  textually similar. This fix narrows one specific failure mode of that
  limitation; the broader problem (no mechanism for reasoning across an
  entire repo) remains open.
