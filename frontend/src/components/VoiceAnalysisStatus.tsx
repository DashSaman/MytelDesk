import { useCallback, useEffect, useState } from 'react';
import { Activity, CheckCircle2, Loader2, RefreshCw, AlertCircle } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { api } from '../lib/api';

interface Capability {
  available: boolean;
  engine: 'silero' | null;
  error_code: string | null;
}

/** Readiness is the server's inference probe, independent of recording policy. */
export function VoiceAnalysisStatus() {
  const { t } = useTranslation();
  const [state, setState] = useState<'checking' | 'ready' | 'unavailable'>('checking');
  const [attempt, setAttempt] = useState(0);
  const retry = useCallback(() => setAttempt(value => value + 1), []);
  useEffect(() => {
    const controller = new AbortController();
    setState('checking');
    api<Capability>('/api/vad/status', { signal: controller.signal })
      .then(result => { if (!controller.signal.aborted) setState(result.available === true && result.engine === 'silero' ? 'ready' : 'unavailable'); })
      .catch(() => { if (!controller.signal.aborted) setState('unavailable'); });
    return () => controller.abort();
  }, [attempt]);
  return (
    <section className="voice-analysis-status" data-vad-state={state} aria-labelledby="voice-analysis-heading">
      <Activity size={20} aria-hidden />
      <div className="voice-analysis-copy">
        <h3 id="voice-analysis-heading">{t('workspace.vadTitle')}</h3>
        <p>{t(state === 'unavailable' ? 'workspace.vadUnavailableHint' : 'workspace.vadDescription')}</p>
        <div className={`voice-analysis-state voice-analysis-state--${state}`} role="status" aria-live="polite">
          {state === 'checking' ? <Loader2 size={15} className="spinner" aria-hidden /> : state === 'ready' ? <CheckCircle2 size={15} aria-hidden /> : <AlertCircle size={15} aria-hidden />}
          {t(state === 'checking' ? 'workspace.vadChecking' : state === 'ready' ? 'workspace.vadReady' : 'workspace.vadUnavailable')}
        </div>
      </div>
      {state === 'unavailable' && <button type="button" className="btn" onClick={retry}><RefreshCw size={14} />{t('common.retry')}</button>}
    </section>
  );
}
