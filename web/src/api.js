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
