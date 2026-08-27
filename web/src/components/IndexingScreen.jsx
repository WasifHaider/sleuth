import { useEffect, useState } from 'react';
import { Link, useParams } from 'react-router-dom';
import { addRepo, getProgress, getRepo } from '../api';

const STEP_LABELS = {
  cloning: 'Clone', cloned: 'Clone',
  parsed: 'Parse',
  chunked: 'Chunk',
  embedding_start: 'Embed', embedding_progress: 'Embed',
  stored: 'Store', ready: 'Store',
};

const ICONS = {
  Clone: <path d="M12 3v11M7.5 10.5 12 15l4.5-4.5M5 18.5h14" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />,
  Parse: <path d="M9 5 4 12l5 7M15 5l5 7-5 7" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />,
  Chunk: <g fill="currentColor"><rect x="4" y="4" width="6" height="6" /><rect x="14" y="4" width="6" height="6" /><rect x="4" y="14" width="6" height="6" /><rect x="14" y="14" width="6" height="6" /></g>,
  Embed: <g fill="currentColor"><circle cx="7" cy="7" r="1.4" /><circle cx="12" cy="7" r="1.4" /><circle cx="17" cy="7" r="1.4" /><circle cx="7" cy="12" r="1.4" /><circle cx="12" cy="12" r="1.4" /><circle cx="17" cy="12" r="1.4" /><circle cx="7" cy="17" r="1.4" /><circle cx="12" cy="17" r="1.4" /><circle cx="17" cy="17" r="1.4" /></g>,
  Store: <path d="M10.5 17a6.5 6.5 0 1 1 0-13 6.5 6.5 0 0 1 0 13ZM15.5 15.5 20 20" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />,
};

const STEPS = [
  { label: 'Clone', title: 'Cloning repository', detail: '' },
  { label: 'Parse', title: 'Parsing to an AST', detail: 'function/class-level structure, not fixed-window splitting' },
  { label: 'Chunk', title: 'Chunking to symbol boundaries', detail: '' },
  { label: 'Embed', title: 'Generating embeddings', detail: 'voyage-code-3, only chunks whose content_hash changed' },
  { label: 'Store', title: 'Storing in pgvector', detail: '' },
];

function stepIndex(step) {
  const label = STEP_LABELS[step] || 'Clone';
  return STEPS.findIndex((s) => s.label === label);
}

function repoName(githubUrl) {
  return githubUrl.replace(/^https?:\/\/github\.com\//, '').replace(/\.git$/, '');
}

function formatElapsed(seconds) {
  const total = Math.max(0, Math.floor(seconds));
  const m = Math.floor(total / 60);
  const s = total % 60;
  return `${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`;
}

function counters(step) {
  if (step === 'Clone') {
    return (d) => (d.files != null ? [['Files found', d.files]] : []);
  }
  if (step === 'Parse') {
    return (d) => (d.parsed != null ? [['Files parsed', d.parsed], ['Skipped (parse errors)', d.skipped]] : []);
  }
  if (step === 'Chunk') {
    return (d) => (d.chunks != null ? [['Chunks created', d.chunks]] : []);
  }
  if (step === 'Embed') {
    return (d) => {
      if (d.done != null) return [['Embedded', `${d.done}/${d.total}`]];
      if (d.to_embed != null) return [['To embed', d.to_embed]];
      return [];
    };
  }
  if (step === 'Store') {
    return (d) => (d.upserted != null ? [['Upserted', d.upserted], ['Skipped (unchanged)', d.skipped_unchanged, 'accent']] : []);
  }
  return () => [];
}

function logLineText(entry) {
  const { step, ...detail } = entry;
  const parts = Object.entries(detail).map(([k, v]) => `${k}=${v}`);
  return parts.length ? `${step}  ${parts.join(' ')}` : step;
}

export default function IndexingScreen() {
  const { repoId } = useParams();
  const [repo, setRepo] = useState(null);
  const [progress, setProgress] = useState(null);
  const [retrying, setRetrying] = useState(false);

  useEffect(() => {
    if (!repoId) return undefined;
    let cancelled = false;
    async function poll() {
      const [r, p] = await Promise.all([getRepo(repoId), getProgress(repoId)]);
      if (!cancelled) {
        setRepo(r);
        setProgress(p);
      }
    }
    poll();
    const interval = setInterval(poll, 1500);
    return () => {
      cancelled = true;
      clearInterval(interval);
    };
  }, [repoId]);

  if (!repoId) {
    return <p style={{ color: 'var(--text-muted)' }}>Select a repo from the Repos screen to watch its indexing progress.</p>;
  }
  if (!repo || !progress) {
    return <p style={{ color: 'var(--text-muted)' }}>Loading…</p>;
  }

  const isQueued = repo.status === 'pending';
  const isReady = repo.status === 'ready';
  const isFailed = repo.status === 'failed';
  const activeIdx = isReady ? STEPS.length : stepIndex(progress.step);

  const bucketDetail = {};
  for (const entry of progress.log) {
    const label = STEP_LABELS[entry.step] || 'Clone';
    const hasDetail = Object.keys(entry).length > 1;
    if (hasDetail || !bucketDetail[label]) bucketDetail[label] = entry;
  }

  const statusWord = isQueued ? 'QUEUED' : isReady ? 'INDEXED' : isFailed ? 'FAILED' : 'INDEXING';
  const statusColor = isQueued ? 'var(--text-muted)' : isReady ? 'var(--text)' : isFailed ? 'var(--status-neutral)' : 'var(--accent)';

  async function handleRetry() {
    setRetrying(true);
    try {
      await addRepo(repo.github_url);
      const [r, p] = await Promise.all([getRepo(repoId), getProgress(repoId)]);
      setRepo(r);
      setProgress(p);
    } finally {
      setRetrying(false);
    }
  }

  return (
    <div style={{ maxWidth: 1100, margin: '0 auto' }}>
      <div className="index-head">
        <div>
          <div className="index-repo-name">{repoName(repo.github_url)}</div>
          <div className="index-status-row">
            <h1 className="index-status-word" style={{ color: statusColor }}>{statusWord}</h1>
            {!isQueued && !isReady && <span className={isFailed ? 'index-status-dot failed' : 'index-status-dot'} />}
          </div>
        </div>
        <div style={{ textAlign: 'right' }}>
          <div className="index-elapsed-label">Elapsed</div>
          <div className="index-elapsed-value">{isQueued ? '—:—' : formatElapsed(progress.elapsed_seconds)}</div>
        </div>
      </div>

      {isQueued && (
        <div className="index-queued-banner">This repo hasn't started indexing yet — it begins automatically in the background.</div>
      )}

      <div className="index-grid">
        <div className="index-steps">
          {STEPS.map((step, i) => {
            const done = i < activeIdx || isReady;
            const active = i === activeIdx && !isReady;
            const failed = isFailed && i === activeIdx;
            const cls = ['index-step', done ? 'done' : active || failed ? 'active' : 'pending'].join(' ');
            const rows = counters(step.label)(bucketDetail[step.label] || {});
            return (
              <div key={step.label} className={cls}>
                <div className="index-step-icon">
                  {active && <span className="index-step-ring" />}
                  {done ? (
                    <span style={{ fontSize: 17, lineHeight: 1 }}>&#10003;</span>
                  ) : (
                    <svg width="20" height="20" viewBox="0 0 24 24">{ICONS[step.label]}</svg>
                  )}
                </div>
                <div style={{ flex: 1, minWidth: 0, paddingTop: 2 }}>
                  <div className="index-step-title">
                    {step.title}
                    {step.detail && <span className="index-step-detail"> — {step.detail}</span>}
                  </div>
                  {rows.length > 0 && (
                    <div className="index-step-counters">
                      {rows.map(([label, value, accent]) => (
                        <div key={label}>
                          <div className="index-counter-label">{label}</div>
                          <div className={accent ? 'index-counter-value accent' : 'index-counter-value'}>{value}</div>
                        </div>
                      ))}
                    </div>
                  )}
                </div>
              </div>
            );
          })}
        </div>

        <div className="log-panel">
          <div className="log-panel-head">Processing log</div>
          <div className="log-panel-body">
            {progress.log.length === 0 ? (
              <div className="log-empty">No activity yet.</div>
            ) : (
              progress.log.map((entry, i) => <div key={i} className="log-line">{logLineText(entry)}</div>)
            )}
          </div>
        </div>
      </div>

      {isReady && (
        <div className="index-banner ready">
          <div>
            <div className="index-banner-title">Ready to chat</div>
            <div className="index-banner-sub">{repoName(repo.github_url)} is fully indexed. Ask Sleuth anything about how it's built.</div>
          </div>
          <Link className="btn-primary" to={`/app/chat/${repo.id}`}>Open chat<span style={{ fontFamily: 'var(--font-mono)', fontSize: 12, marginLeft: 8 }}>&#8594;</span></Link>
        </div>
      )}

      {isFailed && (
        <div className="index-banner failed">
          <div>
            <div className="index-banner-title">Paused at &#8220;{STEPS[activeIdx]?.title || STEPS[0].title}&#8221;</div>
            <div className="index-banner-sub">{repo.error_message || 'Indexing hit an error.'} Retry to run it again from the start.</div>
          </div>
          <button type="button" className="btn-secondary" onClick={handleRetry} disabled={retrying}>
            {retrying ? 'Retrying…' : 'Retry indexing'}
          </button>
        </div>
      )}
    </div>
  );
}
