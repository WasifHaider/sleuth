import { useEffect, useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import { createChat, deleteChat, getMessages, listChats, listRepos, streamChat } from '../api';
import ChatSidebar from './ChatSidebar';
import CodeDrawer from './CodeDrawer';
import Composer from './Composer';
import MessageList from './MessageList';
import { ChatScreenSkeleton } from './Skeleton';

const SUGGESTIONS = [
  'What does this repo do?',
  'Where is the entry point?',
  'How is error handling structured?',
];

function repoName(githubUrl) {
  return githubUrl.replace(/^https?:\/\/github\.com\//, '').replace(/\.git$/, '');
}

export default function ChatScreen() {
  const { repoId } = useParams();
  const navigate = useNavigate();
  const [repos, setRepos] = useState(null);
  const [chats, setChats] = useState([]);
  const [activeChatId, setActiveChatId] = useState(null);
  const [messages, setMessages] = useState([]);
  const [messagesLoading, setMessagesLoading] = useState(false);
  const [streamingText, setStreamingText] = useState(null);
  const [thinking, setThinking] = useState(false);
  const [loadError, setLoadError] = useState(null);
  const [sendError, setSendError] = useState(null);
  const [openSource, setOpenSource] = useState(null);

  useEffect(() => {
    listRepos()
      .then((all) => {
        setRepos(all);
        const ready = all.filter((r) => r.status === 'ready');
        if (!repoId && ready.length > 0) navigate(`/app/chat/${ready[0].id}`, { replace: true });
      })
      .catch((err) => setLoadError(err.message));
  }, []);

  useEffect(() => {
    if (!repoId) return;
    // Reset synchronously before the new repo's chats arrive — otherwise,
    // during the fetch's async gap, the sidebar/header kept showing the
    // PREVIOUS repo's chats/active-chat (or, on failure, stayed on them
    // permanently) as if they belonged to the repo just switched to.
    setChats([]);
    setActiveChatId(null);
    listChats(repoId)
      .then((cs) => {
        setChats(cs);
        setActiveChatId(cs[0]?.id ?? null);
      })
      .catch((err) => setLoadError(err.message));
  }, [repoId]);

  useEffect(() => {
    if (!activeChatId) {
      setMessages([]);
      return;
    }
    setMessagesLoading(true);
    getMessages(activeChatId)
      .then(setMessages)
      .catch((err) => setLoadError(err.message))
      .finally(() => setMessagesLoading(false));
  }, [activeChatId]);

  async function handleNewChat() {
    try {
      const chat = await createChat(repoId);
      setChats((prev) => [{ ...chat, message_count: 0 }, ...prev]);
      setActiveChatId(chat.id);
    } catch (err) {
      // Previously unhandled: a rejected createChat left the '+ New chat'
      // button doing nothing with zero feedback, unlike every other action
      // in this screen.
      setSendError(err.message);
    }
  }

  async function handleDeleteChat(chatId) {
    try {
      await deleteChat(chatId);
      setChats((prev) => prev.filter((c) => c.id !== chatId));
      // Deleting the chat currently open needs to actually leave it — the
      // messages/streaming state below still belongs to a chat that no
      // longer exists otherwise, and re-sending a question against it
      // would 404 the moment it hit POST /chat.
      if (chatId === activeChatId) setActiveChatId(null);
    } catch (err) {
      setSendError(err.message);
    }
  }

  async function handleSend(question) {
    setSendError(null);
    try {
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
      let pendingSources = null;
      let text = '';

      await streamChat(chatId, question, {
        onSources: (sources) => { pendingSources = sources; },
        onTitle: (title) => {
          // First message in a chat: server just renamed it from "New
          // chat" to something derived from the question — reflect that
          // in the sidebar/header immediately instead of waiting for a
          // full chat-list refetch.
          setChats((prev) => prev.map((c) => (c.id === chatId ? { ...c, title } : c)));
        },
        onToken: (token) => {
          setThinking(false);
          text += token;
          setStreamingText(text);
        },
        onDone: () => {
          setMessages((prev) => [...prev, { id: `local-${Date.now()}-a`, role: 'assistant', content: text, sources: pendingSources }]);
          setStreamingText(null);
          setThinking(false);
          setChats((prev) => prev.map((c) => (c.id === chatId ? { ...c, message_count: c.message_count + 2 } : c)));
        },
      });
    } catch (err) {
      // Previously unhandled: any rejection here (network failure, non-ok
      // response, a malformed SSE frame) left `thinking`/`streamingText`
      // stuck permanently true/non-null — the composer stayed disabled
      // forever with no error ever shown.
      setSendError(err.message);
      setThinking(false);
      setStreamingText(null);
    }
  }

  if (repos === null) {
    if (loadError) {
      return <p style={{ color: 'var(--status-neutral)' }}>Couldn't load repos: {loadError}</p>;
    }
    return <ChatScreenSkeleton />;
  }
  const readyRepos = repos.filter((r) => r.status === 'ready');
  if (readyRepos.length === 0) {
    return <p style={{ color: 'var(--text-muted)' }}>No indexed repos yet — add one from the Repos screen first.</p>;
  }

  const activeRepo = repos.find((r) => r.id === repoId);
  const activeChat = chats.find((c) => c.id === activeChatId);

  if (repoId && !activeRepo) {
    // Previously silent: a deep link to a repo id absent from `repos`
    // (deleted, or never belonged to this user in the first place — see
    // the repos.user_id ownership fix) left activeRepo undefined and the
    // header/composer just rendered blank labels with no explanation, so
    // the screen looked broken rather than clearly saying why.
    return <p style={{ color: 'var(--status-neutral)' }}>This repo wasn't found — it may have been removed. <a href="/app/repos">Back to Repos</a></p>;
  }

  return (
    <div className="chat-shell">
      <ChatSidebar
        repos={repos}
        activeRepoId={repoId}
        onSelectRepo={(id) => navigate(`/app/chat/${id}`)}
        chats={chats}
        activeChatId={activeChatId}
        onSelectChat={setActiveChatId}
        onNewChat={handleNewChat}
        onDeleteChat={handleDeleteChat}
      />
      <div className="chat-main">
        <div className="chat-header">
          <span className="dot" style={{ background: 'var(--status-ready)' }} />
          <span className="chat-header-repo">{activeRepo ? repoName(activeRepo.github_url) : ''}</span>
          <span className="chat-header-title">/ {activeChat?.title || 'New chat'}</span>
        </div>
        {sendError && <div className="repo-error-banner" style={{ margin: '0 24px' }}>{sendError}</div>}
        {messagesLoading ? (
          <div className="chat-messages-loading">
            <span className="chat-messages-loading-spinner" />
            <span className="chat-messages-loading-label">Loading chat…</span>
          </div>
        ) : (
        <MessageList
          messages={messages}
          streamingText={streamingText}
          thinking={thinking}
          onOpenSource={setOpenSource}
          emptyState={
            <div className="chat-empty">
              <h1>Ask anything about {activeRepo ? repoName(activeRepo.github_url) : 'this repo'}</h1>
              <p>Sleuth retrieves the relevant chunks before it answers — every claim traces back to a file and a line range.</p>
              <div className="chat-suggestions">
                {SUGGESTIONS.map((q) => (
                  <button key={q} type="button" className="chat-suggestion-pill" onClick={() => handleSend(q)}>{q}</button>
                ))}
              </div>
            </div>
          }
        />
        )}
        <Composer onSend={handleSend} disabled={thinking || streamingText !== null} repoLabel={activeRepo ? repoName(activeRepo.github_url) : ''} />
      </div>
      <CodeDrawer source={openSource} onClose={() => setOpenSource(null)} />
    </div>
  );
}
