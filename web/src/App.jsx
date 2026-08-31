import { createContext, useEffect, useState } from 'react';
import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom';
import AppShell from './components/AppShell';
import LandingPage from './components/LandingPage';
import LoginPage from './components/LoginPage';
import RepoList from './components/RepoList';
import IndexingScreen from './components/IndexingScreen';
import ChatScreen from './components/ChatScreen';
import RepoSettingsScreen from './components/RepoSettingsScreen';
import FullScreenLoader from './components/FullScreenLoader';
import { getMe } from './api';
import './theme.css';

export const AuthContext = createContext(null);

function RequireAuth({ children }) {
  const [status, setStatus] = useState('loading'); // loading | ok | unauth
  const [user, setUser] = useState(null);
  useEffect(() => {
    let cancelled = false;
    getMe()
      .then((u) => { if (!cancelled) { setUser(u); setStatus('ok'); } })
      .catch(() => { if (!cancelled) setStatus('unauth'); });
    // Without this guard, an unmount before getMe() resolves (fast
    // navigation away, or a dev-mode double-invoke) still ran setState on
    // an unmounted component — a real React warning and a wasted render,
    // same pattern IndexingScreen's poll() already guards against.
    return () => { cancelled = true; };
  }, []);
  if (status === 'loading') return <FullScreenLoader label="Checking session…" />;
  if (status === 'unauth') return <Navigate to="/login" replace />;
  return <AuthContext.Provider value={user}>{children}</AuthContext.Provider>;
}

export default function App() {
  return (
    <BrowserRouter>
      <Routes>
        <Route path="/" element={<LandingPage />} />
        <Route path="/login" element={<LoginPage />} />
        <Route
          path="/app"
          element={
            <RequireAuth>
              <AppShell />
            </RequireAuth>
          }
        >
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
