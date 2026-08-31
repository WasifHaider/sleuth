// Full-viewport loading overlay: used for the initial auth check (App.jsx's
// RequireAuth) and for the login/signup transition (LoginPage), so the user
// never sees a raw blank page or a bare "Loading…" line while a session
// round-trip or a redirect is in flight.

export default function FullScreenLoader({ label = 'Loading…' }) {
  return (
    <div className="fullscreen-loader">
      <div className="fullscreen-loader-spinner" aria-hidden="true" />
      <div className="fullscreen-loader-label">{label}</div>
    </div>
  );
}
