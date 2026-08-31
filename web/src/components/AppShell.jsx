import { useContext, useEffect, useState } from 'react';
import { Outlet } from 'react-router-dom';
import { AuthContext } from '../App';
import { updateMe } from '../api';
import NavRail from './NavRail';

export default function AppShell() {
  const user = useContext(AuthContext); // already fetched once by RequireAuth — no second GET /me here
  const [theme, setTheme] = useState(user?.theme_preference || 'storm');

  useEffect(() => {
    document.documentElement.dataset.theme = theme;
    // Cache so main.jsx can apply it synchronously (before the first paint,
    // before RequireAuth's loader even mounts) on the next hard refresh —
    // see main.jsx for the read side of this.
    localStorage.setItem('sleuth_theme', theme);
  }, [theme]);

  function handleThemeChange(name) {
    setTheme(name); // optimistic — switches instantly, doesn't wait on the round-trip
    updateMe({ theme_preference: name }).catch(() => {});
  }

  return (
    <div className="app-shell">
      <NavRail user={user} theme={theme} onThemeChange={handleThemeChange} />
      <div className="app-content">
        <Outlet />
      </div>
    </div>
  );
}
