import { useEffect, useRef, useState } from 'react';

function repoName(githubUrl) {
  return githubUrl.replace(/^https?:\/\/github\.com\//, '').replace(/\.git$/, '');
}

const DAY_MS = 24 * 60 * 60 * 1000;

// Groups chats into Today / Yesterday / Earlier buckets the way the
// design source (docs/design/Sleuth Chat.dc.html) shows them, computed
// from each chat's real created_at instead of mockup-fixed labels.
function groupChatsByDate(chats) {
  const now = new Date();
  const startOfToday = new Date(now.getFullYear(), now.getMonth(), now.getDate()).getTime();
  const groups = { Today: [], Yesterday: [], Earlier: [] };
  for (const c of chats) {
    const created = new Date(c.created_at).getTime();
    const daysAgo = Math.floor((startOfToday - created) / DAY_MS);
    if (daysAgo <= 0) groups.Today.push(c);
    else if (daysAgo === 1) groups.Yesterday.push(c);
    else groups.Earlier.push(c);
  }
  return ['Today', 'Yesterday', 'Earlier']
    .map((label) => ({ label, items: groups[label] }))
    .filter((g) => g.items.length > 0);
}

function PanelLeftIcon() {
  return (
    <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
      <rect x="3" y="4" width="18" height="16" rx="2" />
      <line x1="10" y1="4" x2="10" y2="20" />
    </svg>
  );
}

function PlusIcon() {
  return (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.4">
      <line x1="12" y1="5" x2="12" y2="19" />
      <line x1="5" y1="12" x2="19" y2="12" />
    </svg>
  );
}

function ChevronDownIcon() {
  return (
    <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.4">
      <polyline points="6 9 12 15 18 9" />
    </svg>
  );
}

function TrashIcon() {
  return (
    <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <polyline points="3 6 5 6 21 6" />
      <path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6m5 0V4a2 2 0 0 1 2-2h0a2 2 0 0 1 2 2v2" />
    </svg>
  );
}

export default function ChatSidebar({ repos, activeRepoId, onSelectRepo, chats, activeChatId, onSelectChat, onNewChat, onDeleteChat }) {
  const [collapsed, setCollapsed] = useState(false);
  const [repoMenuOpen, setRepoMenuOpen] = useState(false);
  const [confirmDeleteId, setConfirmDeleteId] = useState(null);
  const repoMenuRef = useRef(null);

  useEffect(() => {
    if (!repoMenuOpen) return undefined;
    function handleClickOutside(e) {
      if (repoMenuRef.current && !repoMenuRef.current.contains(e.target)) setRepoMenuOpen(false);
    }
    document.addEventListener('mousedown', handleClickOutside);
    return () => document.removeEventListener('mousedown', handleClickOutside);
  }, [repoMenuOpen]);

  const activeRepo = repos.find((r) => r.id === activeRepoId);
  // Only ready repos are chattable at all (creating a chat against a
  // non-ready repo 409s server-side) — previously every repo appeared
  // here with non-ready ones merely disabled-and-tooltipped, which read as
  // a broken/dead entry in the list rather than something that just isn't
  // relevant to a repo picker whose only job is "which repo do you want
  // to talk to". Showing only what's actually usable is the plainer UX.
  const otherRepos = repos.filter((r) => r.id !== activeRepoId && r.status === 'ready');
  const groups = groupChatsByDate(chats);

  if (collapsed) {
    return (
      <div className="chat-sidebar chat-sidebar-collapsed">
        <button type="button" className="chat-sidebar-icon-btn" title="Expand" onClick={() => setCollapsed(false)}>
          <PanelLeftIcon />
        </button>
        <button type="button" className="chat-sidebar-icon-btn" title="New chat" onClick={onNewChat}>
          <PlusIcon />
        </button>
      </div>
    );
  }

  return (
    <div className="chat-sidebar">
      <div className="chat-sidebar-section">
        <div className="chat-sidebar-toprow">
          <div className="chat-sidebar-label">Repository</div>
          <button type="button" className="chat-sidebar-icon-btn" title="Collapse" onClick={() => setCollapsed(true)}>
            <PanelLeftIcon />
          </button>
        </div>
        <div className="chat-repo-select" ref={repoMenuRef}>
          <button
            type="button"
            className="chat-repo-select-btn"
            onClick={() => setRepoMenuOpen((v) => !v)}
            disabled={otherRepos.length === 0}
          >
            <span className="chat-repo-select-name">{activeRepo ? repoName(activeRepo.github_url) : 'Select repo'}</span>
            {otherRepos.length > 0 && <ChevronDownIcon />}
          </button>
          {repoMenuOpen && (
            <div className="chat-repo-menu">
              {otherRepos.map((r) => (
                <button
                  key={r.id}
                  type="button"
                  className="chat-repo-menu-item"
                  onClick={() => { onSelectRepo(r.id); setRepoMenuOpen(false); }}
                >
                  {repoName(r.github_url)}
                </button>
              ))}
            </div>
          )}
        </div>
      </div>
      <div className="chat-sidebar-chats">
        <button type="button" className="chat-new-btn" onClick={onNewChat}>
          <PlusIcon /> New chat
        </button>
        {chats.length === 0 && <div className="chat-sidebar-empty">No chats yet.</div>}
        {groups.map((g) => (
          <div key={g.label} className="chat-history-group">
            <div className="chat-sidebar-label">{g.label}</div>
            {g.items.map((c) => (
              <div
                key={c.id}
                className={c.id === activeChatId ? 'chat-history-row active' : 'chat-history-row'}
              >
                <button
                  type="button"
                  className={c.id === activeChatId ? 'chat-history-item active' : 'chat-history-item'}
                  onClick={() => onSelectChat(c.id)}
                >
                  <div className="chat-history-title">{c.title}</div>
                </button>
                {confirmDeleteId === c.id ? (
                  <div className="chat-history-confirm">
                    <button type="button" className="chat-history-confirm-btn" onClick={() => { onDeleteChat(c.id); setConfirmDeleteId(null); }}>
                      Delete
                    </button>
                    <button type="button" className="chat-history-confirm-btn" onClick={() => setConfirmDeleteId(null)}>
                      Cancel
                    </button>
                  </div>
                ) : (
                  <button
                    type="button"
                    className="chat-history-delete-btn"
                    title="Delete chat"
                    onClick={(e) => { e.stopPropagation(); setConfirmDeleteId(c.id); }}
                  >
                    <TrashIcon />
                  </button>
                )}
              </div>
            ))}
          </div>
        ))}
      </div>
    </div>
  );
}
