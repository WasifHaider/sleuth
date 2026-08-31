import { useEffect, useRef, useState } from 'react';
import { Link } from 'react-router-dom';
import { deleteRepo, listRepos } from '../api';
import AddRepoForm from './AddRepoForm';
import RepoStatusBadge from './RepoStatusBadge';
import { RepoListSkeleton } from './Skeleton';

const FILTERS = [
  { key: 'all', label: 'All' },
  { key: 'ready', label: 'Indexed' },
  { key: 'indexing', label: 'Indexing' },
  { key: 'failed', label: 'Failed' },
];

function repoName(githubUrl) {
  return githubUrl.replace(/^https?:\/\/github\.com\//, '').replace(/\.git$/, '');
}

function repoDetail(repo) {
  if (repo.status === 'ready') return repo.embedding_model || 'voyage-code-3';
  return null;
}

function matchesFilter(repo, filter) {
  if (filter === 'all') return true;
  if (filter === 'indexing') return repo.status === 'indexing' || repo.status === 'pending';
  return repo.status === filter;
}

export default function RepoList() {
  const [repos, setRepos] = useState(null);
  const [filter, setFilter] = useState('all');
  const [loadError, setLoadError] = useState(null);
  const [confirmDeleteId, setConfirmDeleteId] = useState(null);
  const [deleting, setDeleting] = useState(false);
  const [deleteError, setDeleteError] = useState(null);
  const reposRef = useRef(null);

  async function refresh() {
    try {
      const data = await listRepos();
      setRepos(data);
      reposRef.current = data;
      setLoadError(null);
    } catch (err) {
      // Without this, a failed fetch (expired session, backend down, a
      // transient network blip) left `repos` at null forever — the
      // skeleton loader would spin indefinitely with the 3s poll silently
      // retrying and failing every time, no error ever surfaced to the user.
      setLoadError(err.message);
    }
  }

  useEffect(() => {
    refresh();
    // Polling every 3s indefinitely, even once every repo has settled into
    // ready/failed with nothing left to change, wastes a request every
    // tick for no benefit — this widens the interval once nothing is
    // pending/indexing, and narrows back to 3s the moment a new repo (via
    // AddRepoForm) puts something back in flight.
    const interval = setInterval(() => {
      const current = reposRef.current;
      const anyInFlight = current === null || current.some((r) => r.status === 'pending' || r.status === 'indexing');
      if (anyInFlight) refresh();
    }, 3000);
    return () => clearInterval(interval);
  }, []);

  async function handleConfirmDelete(repoId) {
    setDeleting(true);
    setDeleteError(null);
    try {
      await deleteRepo(repoId);
      setRepos((prev) => {
        const next = prev.filter((r) => r.id !== repoId);
        reposRef.current = next;
        return next;
      });
      setConfirmDeleteId(null);
    } catch (err) {
      setDeleteError(err.message);
    } finally {
      setDeleting(false);
    }
  }

  if (repos === null) {
    if (loadError) {
      return <p style={{ color: 'var(--status-neutral)' }}>Couldn't load repos: {loadError}</p>;
    }
    return <RepoListSkeleton />;
  }

  const filtered = repos.filter((r) => matchesFilter(r, filter));

  return (
    <div style={{ maxWidth: 1100, margin: '0 auto' }}>
      <div className="repos-head">
        <div>
          <div className="repos-eyebrow">Your repos</div>
          <h1 className="repos-title">Connected repositories</h1>
        </div>
        <a className="btn-primary repos-cta" href="#add-repo">
          Connect a repo<span style={{ fontFamily: 'var(--font-mono)', fontSize: 12 }}>&#8594;</span>
        </a>
      </div>

      <div id="add-repo">
        <AddRepoForm onAdded={(repo) => setRepos((prev) => {
          const next = [repo, ...prev];
          reposRef.current = next;
          return next;
        })} />
      </div>

      {deleteError && <div className="repo-error-banner" style={{ marginBottom: 16 }}>Delete failed: {deleteError}</div>}

      {repos.length === 0 ? (
        <div className="repos-empty">
          <div className="repos-empty-inner">
            <h2>Connect your first repo</h2>
            <p>Sleuth indexes structure, not text, so it can answer questions the moment your repository connects.</p>
          </div>
        </div>
      ) : (
        <>
          <div className="filter-tabs">
            {FILTERS.map((f) => (
              <button
                key={f.key}
                type="button"
                className={filter === f.key ? 'filter-tab active' : 'filter-tab'}
                onClick={() => setFilter(f.key)}
              >
                {f.label}
              </button>
            ))}
          </div>

          <div className="repo-table">
            <div className="repo-table-head">
              <span>Repository</span>
              <span>Status</span>
              <span></span>
            </div>
            {filtered.length === 0 ? (
              <div className="repo-empty-filter">No repos match this filter.</div>
            ) : (
              filtered.map((repo) => {
                const name = repoName(repo.github_url);
                const detail = repoDetail(repo);
                const confirming = confirmDeleteId === repo.id;
                return (
                  <div key={repo.id} className="repo-row">
                    <div className="repo-name-cell repo-status-cell">
                      <span className="repo-icon">GH</span>
                      <div style={{ minWidth: 0 }}>
                        <div className="repo-name">{name}</div>
                        {detail && <div className="repo-detail">{detail}</div>}
                      </div>
                    </div>
                    <div className="repo-status-cell">
                      <RepoStatusBadge status={repo.status} />
                    </div>
                    <div className="repo-action-cell" style={{ display: 'flex', gap: 12, alignItems: 'center', justifyContent: 'flex-end' }}>
                      {confirming ? (
                        <>
                          <span style={{ fontSize: 13, color: 'var(--text-muted)' }}>Delete {name}?</span>
                          <button
                            type="button"
                            className="repo-action repo-action-danger"
                            disabled={deleting}
                            onClick={() => handleConfirmDelete(repo.id)}
                          >
                            {deleting ? 'Deleting…' : 'Confirm'}
                          </button>
                          <button type="button" className="repo-action repo-action-secondary" disabled={deleting} onClick={() => setConfirmDeleteId(null)}>
                            Cancel
                          </button>
                        </>
                      ) : (
                        <>
                          {repo.status === 'ready' && (
                            <Link className="repo-action" to={`/app/chat/${repo.id}`}>Chat</Link>
                          )}
                          {(repo.status === 'indexing' || repo.status === 'pending') && (
                            <Link className="repo-action" to={`/app/indexing/${repo.id}`}>Watch</Link>
                          )}
                          {repo.status === 'failed' && (
                            <Link className="repo-action" to={`/app/indexing/${repo.id}`}>Retry</Link>
                          )}
                          <button
                            type="button"
                            className="repo-action repo-action-secondary"
                            onClick={() => setConfirmDeleteId(repo.id)}
                          >
                            Delete
                          </button>
                        </>
                      )}
                    </div>
                    {repo.status === 'failed' && (
                      <div className="repo-error-banner">{repo.error_message || 'Indexing failed.'}</div>
                    )}
                  </div>
                );
              })
            )}
          </div>
        </>
      )}
    </div>
  );
}
