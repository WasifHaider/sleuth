import { useState } from 'react';
import { addRepo } from '../api';

export default function AddRepoForm({ onAdded }) {
  const [url, setUrl] = useState('');
  const [error, setError] = useState(null);
  const [submitting, setSubmitting] = useState(false);

  async function handleSubmit(e) {
    e.preventDefault();
    const trimmed = url.trim();
    if (!trimmed || submitting) return;
    setSubmitting(true);
    try {
      const repo = await addRepo(trimmed);
      setUrl('');
      setError(null);
      onAdded(repo);
    } catch (err) {
      setError(err.message);
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div style={{ marginBottom: 28 }}>
      <form className="add-repo-form" onSubmit={handleSubmit}>
        <span className="add-repo-arrow">&#8250;</span>
        <input
          type="text"
          placeholder="https://github.com/owner/repo"
          value={url}
          onChange={(e) => { setUrl(e.target.value); setError(null); }}
        />
        <button type="submit" disabled={submitting}>{submitting ? 'Adding…' : 'Add'}</button>
      </form>
      {error && <div className="add-repo-error">{error}</div>}
    </div>
  );
}
