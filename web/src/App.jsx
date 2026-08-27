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
