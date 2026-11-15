import { useState, FormEvent } from 'react';
import { Radio, LogIn, Loader2 } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { setToken, setUser } from '../auth';
import { ThemeToggle } from './ThemeToggle';
import { messageFrom } from '../lib/api';
import { appPath } from '../lib/app-url';
import { Alert, Card, FormField, FormRow } from './ui';

type Props = {
  onSuccess: () => void;
};

export function Login({ onSuccess }: Props) {
  const { t } = useTranslation();
  const [login, setLogin] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  const [source, setSource] = useState<'panel' | 'desk'>(
    () => (localStorage.getItem('opdesk-auth-source') === 'desk' ? 'desk' : 'panel'),
  );
  const pickSource = (s: 'panel' | 'desk') => {
    setSource(s);
    localStorage.setItem('opdesk-auth-source', s);
  };

  const handleSubmit = async (e: FormEvent) => {
    e.preventDefault();
    setError(null);
    if (!login.trim() || !password) {
      setError(t('login.errorEmpty'));
      return;
    }
    setLoading(true);
    try {
      const endpoint = source === 'panel' ? '/api/auth/login-panel' : '/api/auth/login';
      const res = await fetch(appPath(endpoint), {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ login: login.trim(), password }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        setError(messageFrom(data, t('login.errorInvalid')));
        return;
      }
      if (data.access_token) {
        setToken(data.access_token);
        if (data.user) setUser(data.user);
        onSuccess();
      } else {
        setError(t('login.errorInvalidResponse'));
      }
    } catch {
      setError(t('login.errorNetwork'));
    } finally {
      setLoading(false);
    }
  };

  // MyTell workspace v740: presentation only; authentication above is preserved.
  return (
    <div className="login-page">
      <ThemeToggle variant="floating" />
      <div className="desk-login-layout">
        <aside className="desk-login-intro">
          <div className="desk-login-product">
            <img src={(import.meta.env.BASE_URL || '/').replace(/\/$/, '') + '/mytel-emblem.svg'} alt="" />
            <div>
              <strong>{t('app.title')}</strong>
              <span>{t('login.workspaceProduct')}</span>
            </div>
          </div>
          <h1>{t('login.workspaceHeading')}</h1>
          <p>{t('login.workspaceDescription')}</p>
          <div className="desk-login-signal" aria-hidden="true">
            <i /><i /><i /><i /><i /><i /><i /><i /><i /><i /><i /><i />
          </div>
        </aside>
        <Card className="login-panel" icon={Radio} title={t('login.workspaceSignIn')} description={t('login.workspaceWelcome')}>
          <div className="login-source" role="radiogroup" aria-label={t('login.sourceLabel')}
            onKeyDown={(event) => {
              if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return;
              event.preventDefault();
              const next = event.key === 'Home' ? 'panel' : event.key === 'End' ? 'desk' : source === 'panel' ? 'desk' : 'panel';
              pickSource(next);
              event.currentTarget.querySelectorAll<HTMLButtonElement>('button')[next === 'panel' ? 0 : 1]?.focus();
            }}>
            <button type="button" role="radio" aria-checked={source === 'panel'} tabIndex={source === 'panel' ? 0 : -1}
              className={'login-source-btn' + (source === 'panel' ? ' is-active' : '')}
              onClick={() => pickSource('panel')}>{t('login.sourcePanel')}</button>
            <button type="button" role="radio" aria-checked={source === 'desk'} tabIndex={source === 'desk' ? 0 : -1}
              className={'login-source-btn' + (source === 'desk' ? ' is-active' : '')}
              onClick={() => pickSource('desk')}>{t('login.sourceDesk')}</button>
          </div>
          <p className="login-source-hint" id="login-source-hint">
            {t(source === 'panel' ? 'login.workspacePanelHint' : 'login.workspaceDeskHint')}
          </p>
          <form onSubmit={handleSubmit} noValidate>
            <FormRow single>
              <FormField label={t(source === 'panel' ? 'login.workspacePanelUsername' : 'login.extensionLabel')} htmlFor="login-user">
                <input id="login-user" type="text" className="form-input" placeholder={t('login.extensionPlaceholder')}
                  value={login} onChange={(e) => setLogin(e.target.value)} autoComplete="username" autoFocus
                  aria-describedby="login-source-hint" disabled={loading} />
              </FormField>
            </FormRow>
            <FormRow single>
              <FormField label={t('fields.password')} htmlFor="login-password">
                <input id="login-password" type="password" className="form-input" placeholder={t('fields.password')}
                  value={password} onChange={(e) => setPassword(e.target.value)} autoComplete="current-password" disabled={loading} />
              </FormField>
            </FormRow>
            <Alert className="u-mb-4">{error}</Alert>
            <button type="submit" className="btn btn-primary u-w-full" disabled={loading}>
              {loading ? <><Loader2 size={14} className="spinner" />{t('login.signingIn')}</> : <><LogIn size={14} />{t('login.signIn')}</>}
            </button>
          </form>
          <div className="desk-login-footer">
            <a href="/">{t('login.workspaceBack')}</a>
            <span>MyTel</span>
          </div>
        </Card>
      </div>
    </div>
  );
}
