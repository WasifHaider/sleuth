import { useEffect } from 'react';

function highlightClass(line) {
  const trimmed = line.trim();
  if (trimmed.startsWith('//') || trimmed.startsWith('#')) return 'code-line-comment';
  return '';
}

export default function CodeDrawer({ source, onClose }) {
  useEffect(() => {
    function onKey(e) {
      if (e.key === 'Escape') onClose();
    }
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [onClose]);

  if (!source) return null;

  const lines = (source.code_text || '').split('\n');
  const label = source.symbol_name ? `${source.file_path} · ${source.symbol_name}` : source.file_path;

  return (
    <>
      <div className="code-drawer-backdrop" onClick={onClose} />
      <div className="code-drawer">
        <div className="code-drawer-header">
          <div className="code-drawer-title-block">
            <div className="code-drawer-title" title={label}>{label}</div>
            <div className="code-drawer-meta">
              L{source.start_line}–{source.end_line} · {source.kind}
            </div>
          </div>
          <button type="button" className="code-drawer-close" onClick={onClose} aria-label="Close">
            &times;
          </button>
        </div>
        <div className="code-drawer-body">
          {lines.map((line, i) => (
            <div className="code-drawer-line" key={i}>
              <span className="code-drawer-lineno">{source.start_line + i}</span>
              <span className={`code-drawer-linetext ${highlightClass(line)}`}>{line || ' '}</span>
            </div>
          ))}
        </div>
      </div>
    </>
  );
}
