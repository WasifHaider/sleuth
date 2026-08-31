import { useEffect, useRef, useState } from 'react';
import { Link, useParams } from 'react-router-dom';
import { getRepo, retryRepo, streamProgress } from '../api';
import { IndexingScreenSkeleton } from './Skeleton';

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
  const label = STEP_LABELS[step];
  if (label === undefined) return -1;
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
  const [displayElapsed, setDisplayElapsed] = useState(0);
  const [retrying, setRetrying] = useState(false);
  const [streamError, setStreamError] = useState(null);
  const [retryError, setRetryError] = useState(null);
  const [streamGeneration, setStreamGeneration] = useState(0);

  // The server only pushes a "progress" frame when the pipeline's step
  // actually changes (see repos.py::stream_progress) — that's the whole
  // point of switching off a fixed-interval poll. But a step can legitimately
  // run for many seconds (embedding a large batch), and the elapsed-time
  // readout should still visibly tick every second in that gap rather than
  // sitting frozen until the next real event. So the displayed value is
  // computed locally from a (seconds, capturedAt) anchor that's re-synced
  // to the server's real elapsed_seconds on every frame — a local 1s
  // interval just extrapolates forward from that anchor between frames.
  const elapsedAnchorRef = useRef({ seconds: 0, capturedAt: 0 });
  const tickIntervalRef = useRef(null);

  function startTicking() {
    if (tickIntervalRef.current) return;
    tickIntervalRef.current = setInterval(() => {
      const { seconds, capturedAt } = elapsedAnchorRef.current;
      setDisplayElapsed(seconds + (performance.now() - capturedAt) / 1000);
    }, 1000);
  }
  function stopTicking() {
    if (tickIntervalRef.current) {
      clearInterval(tickIntervalRef.current);
      tickIntervalRef.current = null;
    }
  }

  useEffect(() => {
    if (!repoId) return undefined;
    let cancelled = false;

    getRepo(repoId)
      .then((r) => { if (!cancelled) setRepo(r); })
      .catch((err) => { if (!cancelled) setStreamError(err.message); });

    // streamProgress opens one long-lived connection instead of a request
    // every 1.5s; the catch-up frame (see stream_progress's first yield)
    // means this also handles a mid-index page refresh correctly — the new
    // connection gets the current state immediately, not just future
    // changes, so the screen never sits blank/stale after a reload.
    const controller = streamProgress(repoId, {
      onProgress: (p) => {
        if (cancelled) return;
        setProgress(p);
        setStreamError(null);
        elapsedAnchorRef.current = { seconds: p.elapsed_seconds, capturedAt: performance.now() };
        setDisplayElapsed(p.elapsed_seconds);

        const isTerminal = p.step === 'ready' || p.step === 'failed';
        if (isTerminal) {
          stopTicking();
          // The pipeline's own final status/error_message live on the repo
          // row (repos.status/error_message), not in progress_store — refetch
          // once indexing actually finishes so the ready/failed banners below
          // have the real error_message rather than stale queued-state repo data.
          getRepo(repoId).then((r) => { if (!cancelled) setRepo(r); }).catch(() => {});
        } else if (p.step !== 'pending') {
          // Timer starts the moment a real pipeline step is observed —
          // never while the repo is merely queued (step falls back to the
          // repo's own "pending" status until progress_store has an entry).
          startTicking();
        }
      },
      onError: (err) => { if (!cancelled) setStreamError(err.message); },
    });

    return () => {
      cancelled = true;
      stopTicking();
      // Client-side route change (SPA navigation) unmounts this component
      // without a real page reload, which would otherwise leave the fetch
      // running and its reader loop pulling bytes with nothing left to
      // read them — abort explicitly so the connection actually closes.
      controller.abort();
    };
  }, [repoId, streamGeneration]);

  if (!repoId) {
    return <p style={{ color: 'var(--text-muted)' }}>Select a repo from the Repos screen to watch its indexing progress.</p>;
  }
  if (!repo || !progress) {
    if (streamError) {
      return <p style={{ color: 'var(--status-neutral)' }}>Couldn't load indexing status: {streamError}</p>;
    }
    return <IndexingScreenSkeleton />;
  }

  const isQueued = repo.status === 'pending';
  const isReady = repo.status === 'ready';
  const isFailed = repo.status === 'failed';
  const activeIdx = isReady ? STEPS.length : stepIndex(progress.step);

  const bucketDetail = {};
  for (const entry of progress.log) {
    const label = STEP_LABELS[entry.step];
    if (label === undefined) continue;
    const hasDetail = Object.keys(entry).length > 1;
    if (hasDetail || !bucketDetail[label]) bucketDetail[label] = entry;
  }

  const statusWord = isQueued ? 'QUEUED' : isReady ? 'INDEXED' : isFailed ? 'FAILED' : 'INDEXING';
  const statusColor = isQueued ? 'var(--text-muted)' : isReady ? 'var(--text)' : isFailed ? 'var(--status-neutral)' : 'var(--accent)';

  async function handleRetry() {
    setRetrying(true);
    setRetryError(null);
    try {
      // Hits POST /repos/{id}/retry — re-ingests THIS repo row in place.
      // Previously called addRepo(repo.github_url), i.e. POST /repos again
      // with the same URL: create_repo() has no uniqueness constraint on
      // github_url, so every retry of a failed index silently inserted a
      // brand new repo row — the failed one stayed failed forever (nobody
      // was watching it anymore) while a duplicate entry for the same repo
      // piled up in the list on every retry.
      await retryRepo(repoId);
      const r = await getRepo(repoId);
      setRepo(r);
      setProgress(null);
      // Bumps the main effect's dependency to close the old (now-terminal)
      // SSE connection and open a fresh one against the just-restarted
      // pipeline — a stream that already delivered "done" won't reopen
      // itself on its own.
      setStreamGeneration((g) => g + 1);
    } catch (err) {
      // Previously unhandled: addRepo throwing (duplicate URL, malformed
      // github_url now rejected by the backend's validator, a 4xx) left
      // the button stuck on "Retrying…" forever with no explanation.
      setRetryError(err.message);
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
          <div className="index-elapsed-value">{isQueued ? '—:—' : formatElapsed(displayElapsed)}</div>
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
            <div className="index-banner-title">Paused at &#8220;{STEPS[activeIdx]?.title || 'an unrecognized step'}&#8221;</div>
            <div className="index-banner-sub">{repo.error_message || 'Indexing hit an error.'} Retry to run it again from the start.</div>
          </div>
          <button type="button" className="btn-secondary" onClick={handleRetry} disabled={retrying}>
            {retrying ? 'Retrying…' : 'Retry indexing'}
          </button>
        </div>
      )}
      {retryError && (
        <div className="repo-error-banner" style={{ marginTop: 12 }}>Retry failed: {retryError}</div>
      )}
    </div>
  );
}
