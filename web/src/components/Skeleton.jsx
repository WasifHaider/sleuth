// Generic shimmering placeholder blocks. Compose these into per-screen
// layouts below so the loading state roughly matches the real content's
// shape instead of a plain "Loading…" line jumping the layout around.

export function SkeletonBlock({ width, height, radius, style }) {
  return (
    <span
      className="skeleton-block"
      style={{ width, height, borderRadius: radius, ...style }}
    />
  );
}

export function RepoListSkeleton() {
  return (
    <div style={{ maxWidth: 1100, margin: '0 auto' }}>
      <div className="repos-head">
        <div>
          <SkeletonBlock width={90} height={12} style={{ marginBottom: 12 }} />
          <SkeletonBlock width={320} height={38} />
        </div>
        <SkeletonBlock width={150} height={38} radius={2} />
      </div>
      <div className="skeleton-add-repo" />
      <div className="filter-tabs" style={{ opacity: 0.5 }}>
        <SkeletonBlock width={280} height={30} radius={999} />
      </div>
      <div className="repo-table">
        <div className="repo-table-head">
          <span /><span /><span />
        </div>
        {[0, 1, 2].map((i) => (
          <div key={i} className="repo-row">
            <div className="repo-name-cell">
              <SkeletonBlock width={20} height={20} radius={3} />
              <SkeletonBlock width={180} height={15} />
            </div>
            <SkeletonBlock width={70} height={22} radius={999} />
            <SkeletonBlock width={60} height={30} radius={2} />
          </div>
        ))}
      </div>
    </div>
  );
}

export function ChatScreenSkeleton() {
  return (
    <div className="chat-shell">
      <div className="chat-sidebar">
        <div className="chat-sidebar-section">
          <SkeletonBlock width={80} height={11} style={{ marginBottom: 10 }} />
          <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
            <SkeletonBlock width={90} height={26} radius={999} />
            <SkeletonBlock width={70} height={26} radius={999} />
          </div>
        </div>
        <div className="chat-sidebar-chats">
          <SkeletonBlock width="100%" height={38} radius={2} style={{ marginBottom: 16 }} />
          <SkeletonBlock width={50} height={11} style={{ marginBottom: 10 }} />
          {[0, 1, 2].map((i) => (
            <SkeletonBlock key={i} width="100%" height={42} radius={2} style={{ marginBottom: 4 }} />
          ))}
        </div>
      </div>
      <div className="chat-main">
        <div className="chat-header">
          <SkeletonBlock width={10} height={10} radius="50%" />
          <SkeletonBlock width={120} height={13} />
        </div>
        <div className="chat-empty">
          <SkeletonBlock width={360} height={30} />
          <SkeletonBlock width={480} height={15} />
        </div>
      </div>
    </div>
  );
}

export function IndexingScreenSkeleton() {
  return (
    <div style={{ maxWidth: 1100, margin: '0 auto' }}>
      <div className="index-head">
        <div>
          <SkeletonBlock width={140} height={12} style={{ marginBottom: 12 }} />
          <SkeletonBlock width={220} height={48} />
        </div>
        <div style={{ textAlign: 'right' }}>
          <SkeletonBlock width={60} height={11} style={{ marginBottom: 8, marginLeft: 'auto' }} />
          <SkeletonBlock width={80} height={26} style={{ marginLeft: 'auto' }} />
        </div>
      </div>
      <div className="index-grid">
        <div className="index-steps">
          {[0, 1, 2, 3, 4].map((i) => (
            <div key={i} className="index-step pending">
              <SkeletonBlock width={40} height={40} radius={2} />
              <div style={{ flex: 1, paddingTop: 2 }}>
                <SkeletonBlock width={160} height={16} />
              </div>
            </div>
          ))}
        </div>
        <div className="log-panel">
          <div className="log-panel-head"><SkeletonBlock width={100} height={11} /></div>
          <div className="log-panel-body">
            {[0, 1, 2].map((i) => (
              <SkeletonBlock key={i} width="90%" height={11} style={{ marginBottom: 7 }} />
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}
