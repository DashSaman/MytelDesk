/**
 * MyTellPBX v7.3.0 — compact theme toggle (Sun/Moon).
 * Controlled when App supplies props; self-managing otherwise (login
 * screen) against the same 'opdesk:theme' storage + <html data-theme>
 * the app engine uses — one source of truth, no divergence.
 */
import { useEffect, useState } from 'react';
import { Sun, Moon } from 'lucide-react';
import { useTranslation } from 'react-i18next';

const THEME_KEY = 'opdesk:theme';

type Props = {
  theme?: 'dark' | 'light';
  onToggle?: (next: 'dark' | 'light') => void;
  variant?: 'header' | 'floating';
};

function currentTheme(): 'dark' | 'light' {
  try {
    const v = localStorage.getItem(THEME_KEY);
    if (v === 'dark' || v === 'light') return v;
  } catch { /* ignore */ }
  if (typeof window !== 'undefined' && window.matchMedia
      && window.matchMedia('(prefers-color-scheme: light)').matches) return 'light';
  return 'dark';
}

export function ThemeToggle({ theme, onToggle, variant = 'header' }: Props) {
  const { t } = useTranslation();
  const [self, setSelf] = useState<'dark' | 'light'>(() =>
    typeof document !== 'undefined'
      ? ((document.documentElement.dataset.theme as 'dark' | 'light') || currentTheme())
      : 'dark');
  useEffect(() => {
    if (theme) setSelf(theme);
  }, [theme]);
  const active = theme ?? self;
  const dark = active === 'dark';
  const click = () => {
    const next: 'dark' | 'light' = dark ? 'light' : 'dark';
    if (onToggle) {
      onToggle(next);
    } else {
      document.documentElement.dataset.theme = next;
      try { localStorage.setItem(THEME_KEY, next); } catch { /* ignore */ }
      setSelf(next);
    }
  };
  return (
    <button
      type="button"
      className={'theme-toggle' + (variant === 'floating' ? ' theme-toggle-floating' : '')}
      onClick={click}
      aria-pressed={dark}
      aria-label={dark ? t('header.switchLight', 'تغییر به حالت روشن') : t('header.switchDark', 'تغییر به حالت تاریک')}
      title={dark ? t('header.switchLight', 'تغییر به حالت روشن') : t('header.switchDark', 'تغییر به حالت تاریک')}
    >
      {dark ? <Sun size={16} aria-hidden="true" /> : <Moon size={16} aria-hidden="true" />}
    </button>
  );
}
