import { useEffect, useRef, useState } from 'react';
import { NavLink } from 'react-router-dom';
import { logout } from '../api';
import Logo from './Logo';

const NAV_ITEMS = [
  { to: '/app/chat', label: 'Chat' },
  { to: '/app/repos', label: 'Repos' },
  { to: '/app/indexing', label: 'Indexing status' },
  { to: '/app/settings', label: 'Settings' },
];

// Kept in sync with sleuth/api/schemas.py's Theme literal — only these two
// themes are implemented/offered now (see theme.css).
const THEMES = ['storm', 'ivory'];

function initials(user) {
  const source = user?.name || user?.email || '?';
  return source.trim().slice(0, 1).toUpperCase();
}

export default function NavRail({ user, theme, onThemeChange }) {
  const [menuOpen, setMenuOpen] = useState(false);
  const menuRef = useRef(null);

  useEffect(() => {
    if (!menuOpen) return undefined;
    function handleClickOutside(e) {
      if (menuRef.current && !menuRef.current.contains(e.target)) setMenuOpen(false);
    }
    document.addEventListener('mousedown', handleClickOutside);
    return () => document.removeEventListener('mousedown', handleClickOutside);
  }, [menuOpen]);

  return (
    <nav className="sleuth-rail">
      <NavLink to="/app/repos" className="rail-logo" title="Sleuth dashboard">
        <Logo />
      </NavLink>
      <div className="rail-nav">
        {NAV_ITEMS.map((item) => (
          <NavLink key={item.to} to={item.to} className={({ isActive }) => (isActive ? 'navitem active' : 'navitem')}>
            {item.label}
          </NavLink>
        ))}
      </div>
      <div className="rail-account" ref={menuRef}>
        <button type="button" className="rail-account-btn" onClick={() => setMenuOpen((v) => !v)}>
          <span className="rail-avatar">{initials(user)}</span>
          <span className="rail-account-name">{user?.name || user?.email || 'Account'}</span>
        </button>
        {menuOpen && (
          <div className="rail-account-menu">
            <div className="rail-theme-label">Theme</div>
            <div className="rail-theme-swatches">
              {THEMES.map((name) => (
                <button
                  key={name}
                  type="button"
                  className={name === theme ? 'rail-theme-swatch active' : 'rail-theme-swatch'}
                  onClick={() => onThemeChange(name)}
                >
                  {name[0].toUpperCase() + name.slice(1)}
                </button>
              ))}
            </div>
            <div className="rail-menu-divider" />
            <button type="button" className="rail-menu-link" onClick={() => logout().catch(() => {}).then(() => window.location.assign('/login'))}>
              Log out
            </button>
          </div>
        )}
      </div>
    </nav>
  );
}
