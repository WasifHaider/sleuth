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

export async function logout() {
  const res = await fetch(apiUrl('/auth/logout'), { method: 'POST', credentials: 'include' });
  if (!res.ok) throw new Error(`logout failed: ${res.status}`);
  return res.json();
}
