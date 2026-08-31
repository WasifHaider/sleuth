import { useState } from 'react';

export default function Composer({ onSend, disabled, repoLabel }) {
  const [draft, setDraft] = useState('');
  const canSend = draft.trim().length > 0 && !disabled;

  function handleSubmit(e) {
    e.preventDefault();
    if (!canSend) return;
    onSend(draft.trim());
    setDraft('');
  }

  return (
    <div className="chat-composer">
      <div className="chat-composer-status">
        <span className="dot" style={{ background: 'var(--accent)' }} />
        {repoLabel}
      </div>
      <form className="chat-composer-form" onSubmit={handleSubmit}>
        <input
          type="text"
          className="input-mono"
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          disabled={disabled}
          placeholder="Ask about this repository…"
        />
        <button type="submit" className="btn-primary" disabled={!canSend}>&#8593;</button>
      </form>
    </div>
  );
}
