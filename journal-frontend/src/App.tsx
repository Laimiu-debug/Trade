import { useEffect, useState } from 'react';
import { NavLink, Route, Routes, useLocation } from 'react-router-dom';
import Dashboard from './pages/Dashboard';
import Journal from './pages/Journal';
import WeeklyReview from './pages/WeeklyReview';
import MonthlyReview from './pages/MonthlyReview';
import Trades from './pages/Trades';
import Capital from './pages/Capital';
import Stats from './pages/Stats';
import Cards from './pages/Cards';
import Settings from './pages/Settings';
import { applyTheme, readTheme, ThemeContext, type Theme } from './theme';


const NAV = [
  { to: '/', label: '总览', icon: 'M3 13h4v8H3zM10 8h4v13h-4zM17 3h4v18h-4z' },
  { to: '/journal', label: '每日复盘', icon: 'M4 4h16v16H4zM8 2v4M16 2v4M4 10h16' },
  { to: '/weekly', label: '周复盘', icon: 'M12 8v4l3 3M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0' },
  { to: '/monthly', label: '月复盘', icon: 'M4 4h16v16H4zM8 2v4M16 2v4M4 10h16M8 14h8M8 18h5' },
  { to: '/trades', label: '交易记录', icon: 'M3 17l6-6 4 4 8-8M17 7h4v4' },
  { to: '/capital', label: '资金账本', icon: 'M12 1v22M17 5H9.5a3.5 3.5 0 0 0 0 7h5a3.5 3.5 0 0 1 0 7H6' },
  { to: '/stats', label: '统计分析', icon: 'M18 20V10M12 20V4M6 20v-6' },
  { to: '/cards', label: '灵感闪记', icon: 'M12 2l2.4 7.2H22l-6 4.6 2.3 7.2-6.3-4.5-6.3 4.5L8 13.8 2 9.2h7.6z' },
  { to: '/settings', label: '设置', icon: 'M12 15a3 3 0 1 0 0-6 3 3 0 0 0 0 6zM19 12a7 7 0 0 1-.1 1.2l2 1.6-2 3.4-2.4-1a7 7 0 0 1-2 1.2L14 21h-4l-.4-2.6a7 7 0 0 1-2.1-1.2l-2.4 1-2-3.4 2-1.6A7 7 0 0 1 5 12c0-.4 0-.8.1-1.2l-2-1.6 2-3.4 2.4 1a7 7 0 0 1 2-1.2L10 3h4l.4 2.6a7 7 0 0 1 2.1 1.2l2.4-1 2 3.4-2 1.6c.1.4.1.8.1 1.2z' },
];

export default function App() {
  const location = useLocation();
  const [theme, setTheme] = useState<Theme>(readTheme);
  const [navOpen, setNavOpen] = useState(false);

  useEffect(() => { applyTheme(theme); }, [theme]);
  // The workspace shell embeds this app in an iframe and shares the theme key.
  useEffect(() => {
    const update = () => setTheme(readTheme());
    const media = window.matchMedia?.('(prefers-color-scheme: dark)');
    window.addEventListener('storage', update);
    media?.addEventListener('change', update);
    return () => {
      window.removeEventListener('storage', update);
      media?.removeEventListener('change', update);
    };
  }, []);
  useEffect(() => { setNavOpen(false); }, [location.pathname]);

  const toggleTheme = () => {
    const next: Theme = theme === 'dark' ? 'light' : 'dark';
    localStorage.setItem('trade-theme-mode', next);
    localStorage.setItem('lt-theme', next);
    applyTheme(next);
    setTheme(next);
  };

  return (
    <ThemeContext.Provider value={theme}>
    <div className={`app-shell${navOpen ? ' sidebar-open' : ''}`}>
      <button
        type="button"
        className="sidebar-backdrop no-print"
        aria-label="关闭导航"
        onClick={() => setNavOpen(false)}
      />
      <aside className="sidebar no-print">
        <div className="brand">
          <div className="brand-row">
            <img className="brand-logo" src="/journal-app/trade-mark.svg" alt="Trade" />
            <h1 className="brand-title">Trade</h1>
          </div>
          <div className="brand-sub">交易与复盘工作台</div>
        </div>
        {NAV.map(item => (
          <NavLink
            key={item.to}
            to={item.to}
            end={item.to === '/'}
            className={({ isActive }) => `nav-link${isActive ? ' active' : ''}`}
          >
            <svg className="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
              <path d={item.icon} />
            </svg>
            {item.label}
          </NavLink>
        ))}
        <div className="nav-footer">
          <button type="button" className="nav-theme no-print" onClick={toggleTheme} title="切换深色/浅色主题">
            {theme === 'dark' ? '浅色' : '深色'}
          </button>
          <button type="button" className="nav-quit no-print" onClick={() => { window.top?.location.assign('/screener'); }}>返回交易工作台</button>
        </div>
      </aside>
      <main className="main">
        <div className="main-topbar no-print">
          <button type="button" className="nav-toggle" aria-label="打开导航" onClick={() => setNavOpen(true)}>
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
              <path d="M4 6h16M4 12h16M4 18h16" />
            </svg>
          </button>
          <span className="muted">工作空间 / {NAV.find(item => item.to === location.pathname)?.label || '交易复盘'}</span>
        </div>
        <Routes>
          <Route path="/" element={<Dashboard />} />
          <Route path="/journal" element={<Journal />} />
          <Route path="/weekly" element={<WeeklyReview />} />
          <Route path="/monthly" element={<MonthlyReview />} />
          <Route path="/trades" element={<Trades />} />
          <Route path="/capital" element={<Capital />} />
          <Route path="/stats" element={<Stats />} />
          <Route path="/cards" element={<Cards />} />
          <Route path="/settings" element={<Settings />} />
        </Routes>
      </main>

    </div>
    </ThemeContext.Provider>
  );
}
