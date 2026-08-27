import { useEffect, useState } from 'react';
import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom';
import AppShell from './components/AppShell';
import LandingPage from './components/LandingPage';
import LoginPage from './components/LoginPage';
import RepoList from './components/RepoList';
import IndexingScreen from './components/IndexingScreen';
import ChatScreen from './components/ChatScreen';
import RepoSettingsScreen from './components/RepoSettingsScreen';
import { getMe } from './api';
import './theme.css';

function RequireAuth({ children }) {
  const [status, setStatus] = useState('loading'); // loading | ok | unauth
  useEffect(() => {
    getMe().then(() => setStatus('ok')).catch(() => setStatus('unauth'));
  }, []);
  if (status === 'loading') return null;
  if (status === 'unauth') return <Navigate to="/login" replace />;
  return children;
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
