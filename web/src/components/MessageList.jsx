import { useState } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { LogoMarkPulse } from './Logo';

const COLLAPSED_SOURCE_COUNT = 4;

function SourcePill({ source, onOpen }) {
  const label = source.symbol_name ? `${source.file_path} · ${source.symbol_name}` : source.file_path;
  return (
    <button type="button" className="chat-source-pill" onClick={() => onOpen(source)} title={`Open ${label}`}>
      <span className="dot" />
      <span className="chat-source-file">{label}</span>
      <span className="chat-source-lines">L{source.start_line}–{source.end_line}</span>
      {source.is_doc && <span className="chat-source-doc-badge">docs</span>}
    </button>
  );
}

function SourcesBlock({ sources, onOpenSource }) {
  const [expanded, setExpanded] = useState(false);
  const shown = expanded ? sources : sources.slice(0, COLLAPSED_SOURCE_COUNT);
  const hiddenCount = sources.length - shown.length;
  return (
    <div className="chat-sources">
      <div className="chat-sources-label">Sources ({sources.length})</div>
      <div className="chat-sources-list">
        {shown.map((s, i) => (
          <SourcePill key={i} source={s} onOpen={onOpenSource} />
        ))}
      </div>
      {hiddenCount > 0 && (
        <button type="button" className="chat-sources-toggle" onClick={() => setExpanded(true)}>
          + {hiddenCount} more source{hiddenCount === 1 ? '' : 's'}
        </button>
      )}
      {expanded && sources.length > COLLAPSED_SOURCE_COUNT && (
        <button type="button" className="chat-sources-toggle" onClick={() => setExpanded(false)}>
          Show less
        </button>
      )}
    </div>
  );
}

function MessageRow({ role, content, sources, thinking, streaming, onOpenSource }) {
  const isUser = role === 'user';
  return (
    <div className={isUser ? 'chat-row user' : 'chat-row assistant'}>
      <div className={isUser ? 'chat-bubble user' : 'chat-bubble assistant'}>
        {thinking ? (
          <span className="chat-thinking-row">
            <LogoMarkPulse />
            <span className="chat-thinking">searching the repository</span>
          </span>
        ) : isUser ? (
          content
        ) : (
          <div className="chat-markdown">
            <ReactMarkdown remarkPlugins={[remarkGfm]}>{content}</ReactMarkdown>
            {streaming && <span className="chat-caret" />}
          </div>
        )}
      </div>
      {sources && sources.length > 0 && <SourcesBlock sources={sources} onOpenSource={onOpenSource} />}
    </div>
  );
}

export default function MessageList({ messages, streamingText, thinking, emptyState, onOpenSource }) {
  if (messages.length === 0 && streamingText === null && !thinking) {
    return emptyState;
  }
  return (
    <div className="chat-messages">
      {messages.map((m) => (
        <MessageRow key={m.id} role={m.role} content={m.content} sources={m.sources} onOpenSource={onOpenSource} />
      ))}
      {thinking && <MessageRow role="assistant" content="" thinking />}
      {!thinking && streamingText !== null && (
        <MessageRow role="assistant" content={streamingText} streaming onOpenSource={onOpenSource} />
      )}
    </div>
  );
}
