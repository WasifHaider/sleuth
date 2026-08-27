import { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { listRepos } from '../api';
import AddRepoForm from './AddRepoForm';
import RepoStatusBadge from './RepoStatusBadge';

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
  if (repo.status === 'failed') return repo.error_message || 'indexing failed';
  if (repo.status === 'ready') return repo.embedding_model || 'voyage-code-3';
  return null;
}

function matchesFilter(repo, filter) {
  if (filter === 'all') return true;
  if (filter === 'indexing') return repo.status === 'indexing' || repo.status === 'pending';
  return repo.status === filter;
}

export default function RepoList() {
  const [repos, setRepos] = useState([]);
  const [filter, setFilter] = useState('all');

  async function refresh() {
    setRepos(await listRepos());
  }

  useEffect(() => {
    refresh();
    const interval = setInterval(refresh, 3000);
    return () => clearInterval(interval);
  }, []);

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
        <AddRepoForm onAdded={(repo) => setRepos((prev) => [repo, ...prev])} />
      </div>

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
                    <div className="repo-action-cell">
                      {repo.status === 'ready' && (
                        <Link className="repo-action" to={`/app/chat/${repo.id}`}>Chat</Link>
                      )}
                      {(repo.status === 'indexing' || repo.status === 'pending') && (
                        <Link className="repo-action" to={`/app/indexing/${repo.id}`}>Watch</Link>
                      )}
                    </div>
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
