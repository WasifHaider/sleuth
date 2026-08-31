import { createRoot } from 'react-dom/client'
import App from './App.jsx'

// Apply the last-known theme synchronously, before React's first paint.
// Without this, a hard refresh always showed the default storm theme on
// RequireAuth's "Checking session…" loader (and briefly on AppShell itself)
// regardless of the signed-in user's actual theme_preference, because
// data-theme was only ever set inside AppShell's effect — which doesn't run
// until *after* the /me round-trip resolves and AppShell mounts. AppShell
// keeps this cache updated (see AppShell.jsx) every time the theme changes.
const cachedTheme = localStorage.getItem('sleuth_theme');
document.documentElement.dataset.theme = cachedTheme || 'storm';

createRoot(document.getElementById('root')).render(<App />)
