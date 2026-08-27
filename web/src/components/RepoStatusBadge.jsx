const LABELS = { ready: 'Indexed', indexing: 'Indexing', pending: 'Not indexed yet', failed: 'Failed' };

export default function RepoStatusBadge({ status }) {
  const cls =
    status === 'ready' ? 'pill pill-ready'
    : status === 'indexing' ? 'pill pill-indexing'
    : status === 'failed' ? 'pill pill-failed'
    : 'pill pill-pending';

  return (
    <span className={cls}>
      {status === 'indexing' && <span className="dot dot-pulse" style={{ background: 'currentColor' }} />}
      {LABELS[status] || status}
    </span>
  );
}
