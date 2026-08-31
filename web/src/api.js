const BASE_URL = import.meta.env.VITE_API_URL || 'http://localhost:8000';

export function apiUrl(path) {
  return `${BASE_URL}${path}`;
}

export async function apiGet(path) {
  const res = await fetch(apiUrl(path), { credentials: 'include' });
  if (!res.ok) throw new Error(`GET ${path} failed: ${res.status}`);
  return res.json();
}

export async function apiPost(path, body) {
  const res = await fetch(apiUrl(path), {
    method: 'POST',
    credentials: 'include',
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

export async function apiPatch(path, body) {
  const res = await fetch(apiUrl(path), {
    method: 'PATCH',
    credentials: 'include',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  if (!res.ok) {
    const detail = await res.json().catch(() => ({}));
    const err = new Error(detail.detail || `PATCH ${path} failed: ${res.status}`);
    err.status = res.status;
    throw err;
  }
  return res.json();
}

export async function logout() {
  const res = await fetch(apiUrl('/auth/logout'), { method: 'POST', credentials: 'include' });
  if (!res.ok) throw new Error(`logout failed: ${res.status}`);
  return res.json();
}

export function getMe() {
  return apiGet('/me');
}

export function updateMe(patch) {
  return apiPatch('/me', patch);
}

export function login(email, password) {
  return apiPost('/auth/login', { email, password });
}

export function signup(email, password, name) {
  return apiPost('/auth/signup', { email, password, name });
}

export function listRepos() {
  return apiGet('/repos');
}

export function addRepo(githubUrl) {
  return apiPost('/repos', { github_url: githubUrl });
}

export function getRepo(repoId) {
  return apiGet(`/repos/${repoId}`);
}

export function getProgress(repoId) {
  return apiGet(`/repos/${repoId}/progress`);
}

export async function retryRepo(repoId) {
  const res = await fetch(apiUrl(`/repos/${repoId}/retry`), { method: 'POST', credentials: 'include' });
  if (!res.ok) {
    const detail = await res.json().catch(() => ({}));
    throw new Error(detail.detail || `retry failed: ${res.status}`);
  }
  return res.json();
}

export async function deleteRepo(repoId) {
  const res = await fetch(apiUrl(`/repos/${repoId}`), { method: 'DELETE', credentials: 'include' });
  if (!res.ok) {
    const detail = await res.json().catch(() => ({}));
    throw new Error(detail.detail || `delete failed: ${res.status}`);
  }
  return res.json();
}

export async function deleteChat(chatId) {
  const res = await fetch(apiUrl(`/chats/${chatId}`), { method: 'DELETE', credentials: 'include' });
  if (!res.ok) {
    const detail = await res.json().catch(() => ({}));
    throw new Error(detail.detail || `delete failed: ${res.status}`);
  }
  return res.json();
}

export function listChats(repoId) {
  return apiGet(`/chats?repo_id=${repoId}`);
}

export function createChat(repoId) {
  return apiPost('/chats', { repo_id: repoId });
}

export function getMessages(chatId) {
  return apiGet(`/chats/${chatId}/messages`);
}

export async function streamChat(chatId, question, { onSources, onToken, onDone, onTitle }) {
  const res = await fetch(apiUrl('/chat'), {
    method: 'POST',
    credentials: 'include',
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
      const eventType = eventLine ? eventLine.slice('event: '.length) : 'message';
      // A token can span multiple "data: " lines (SSE can't carry a raw
      // newline on one data line) — rejoin them with '\n' to recover it.
      const data = lines
        .filter((l) => l.startsWith('data: '))
        .map((l) => l.slice('data: '.length))
        .join('\n');

      if (eventType === 'sources') {
        try {
          onSources(JSON.parse(data));
        } catch {
          // A malformed/partial JSON payload (server error mid-stream, or a
          // frame split oddly) used to throw straight out of this read
          // loop uncaught — ChatScreen's handleSend had no catch either,
          // so `thinking`/`streamingText` got stuck true forever with no
          // error ever shown. Not fatal to the rest of the stream: just
          // skip this one malformed sources frame and keep reading tokens.
        }
      } else if (eventType === 'title') {
        try {
          onTitle?.(JSON.parse(data).title);
        } catch {
          // Same tolerance as the sources frame above — a bad title frame
          // shouldn't derail the rest of the answer stream.
        }
      } else if (eventType === 'done') onDone();
      else onToken(data);
    }
  }
}
