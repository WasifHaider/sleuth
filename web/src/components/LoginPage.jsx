import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { login, signup } from '../api';
import FullScreenLoader from './FullScreenLoader';

export default function LoginPage() {
  const navigate = useNavigate();
  const [mode, setMode] = useState('login'); // login | signup
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [name, setName] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  // Distinct from `busy`: stays true across the post-auth navigate, so the
  // full-screen loader keeps covering the screen until AppShell/RequireAuth
  // has actually mounted the Repos screen behind it.
  const [entering, setEntering] = useState(false);

  async function handleSubmit(e) {
    e.preventDefault();
    if (busy) return;
    setBusy(true);
    setError(null);
    try {
      if (mode === 'login') {
        await login(email.trim(), password);
      } else {
        await signup(email.trim(), password, name.trim() || null);
      }
      setEntering(true);
      navigate('/app', { replace: true });
    } catch (err) {
      setError(err.message);
      setBusy(false);
    }
  }

  if (entering) {
    return <FullScreenLoader label={mode === 'login' ? 'Logging in…' : 'Setting up your account…'} />;
  }

  return (
    <div className="login-shell">
      <div className="login-card card">
        <h1>{mode === 'login' ? 'Log in to Sleuth' : 'Create your Sleuth account'}</h1>
        {error && <div className="login-error">{error}</div>}
        <form onSubmit={handleSubmit}>
          {mode === 'signup' && (
            <input
              type="text"
              value={name}
              disabled={busy}
              onChange={(e) => setName(e.target.value)}
              placeholder="Name (optional)"
              className="input-mono"
            />
          )}
          <input
            type="email"
            required
            value={email}
            disabled={busy}
            onChange={(e) => setEmail(e.target.value)}
            placeholder="you@company.com"
            className="input-mono"
          />
          <input
            type="password"
            required
            minLength={8}
            value={password}
            disabled={busy}
            onChange={(e) => setPassword(e.target.value)}
            placeholder="Password"
            className="input-mono"
          />
          <button type="submit" className="btn-primary" disabled={busy || !email.trim() || !password}>
            {busy ? 'Please wait…' : mode === 'login' ? 'Log in' : 'Sign up'}
          </button>
        </form>
        <button
          type="button"
          className="login-switch"
          onClick={() => {
            setMode(mode === 'login' ? 'signup' : 'login');
            setError(null);
          }}
        >
          {mode === 'login' ? "Don't have an account? Sign up" : 'Already have an account? Log in'}
        </button>
      </div>
    </div>
  );
}
