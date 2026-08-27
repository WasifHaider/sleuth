import { NavLink } from 'react-router-dom';
import { logout } from '../api';

const NAV_ITEMS = [
  { to: '/app/chat', label: 'Chat' },
  { to: '/app/repos', label: 'Repos' },
  { to: '/app/indexing', label: 'Indexing status' },
  { to: '/app/settings', label: 'Settings' },
];

export default function NavRail() {
  return (
    <nav className="sleuth-rail">
      <NavLink to="/app/repos" className="rail-logo" title="Sleuth dashboard">
        <span className="logo-mark" />
      </NavLink>
      <div className="rail-nav">
        {NAV_ITEMS.map((item) => (
          <NavLink key={item.to} to={item.to} className={({ isActive }) => (isActive ? 'navitem active' : 'navitem')}>
            {item.label}
          </NavLink>
        ))}
      </div>
      <div className="rail-account">
        <button type="button" onClick={() => logout().then(() => window.location.assign('/login'))}>
          Log out
        </button>
      </div>
    </nav>
  );
}
