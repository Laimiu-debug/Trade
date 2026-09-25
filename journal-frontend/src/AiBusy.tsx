import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from 'react';
import { api } from './api';

export type AiBusyMode = 'overlay' | 'banner';

export const SETTINGS_UPDATED_EVENT = 'lt-settings-updated';

type AiBusyCtx = {
  busy: boolean;
  label: string;
  mode: AiBusyMode;
  setBusy: (on: boolean, label?: string) => void;
};

const Ctx = createContext<AiBusyCtx>({
  busy: false,
  label: '',
  mode: 'banner',
  setBusy: () => {},
});

function parseBusyMode(raw: string | undefined): AiBusyMode {
  return raw === 'overlay' ? 'overlay' : 'banner';
}

function AiBusyIndicator({ mode, label }: { mode: AiBusyMode; label: string }) {
  switch (mode) {
    case 'overlay':
      return (
        <div className="ai-busy-overlay no-print" role="status" aria-live="polite">
          <div className="ai-busy-card">
            <div className="quit-spinner" />
            <div className="ai-busy-text">{label}</div>
            <div className="ai-busy-sub">请稍候，不要关闭窗口</div>
          </div>
        </div>
      );
    case 'banner':
      return (
        <div className="ai-busy-banner no-print" role="status" aria-live="polite">
          <div className="ai-busy-banner-track" aria-hidden="true">
            <div className="ai-busy-banner-progress" />
          </div>
          <div className="ai-busy-banner-chip">
            <span className="ai-busy-pulse" aria-hidden="true" />
            <span className="ai-busy-banner-text">{label}</span>
            <span className="ai-busy-banner-sep" aria-hidden="true" />
            <span className="ai-busy-banner-sub">可继续浏览</span>
          </div>
        </div>
      );
    default: {
      const _exhaustive: never = mode;
      return _exhaustive;
    }
  }
}

export function AiBusyProvider({ children }: { children: ReactNode }) {
  const [busy, setBusyState] = useState(false);
  const [label, setLabel] = useState('AI 处理中…');
  const [mode, setMode] = useState<AiBusyMode>('banner');

  const loadMode = useCallback(() => {
    api.get<{ ai_busy_mode?: string }>('/api/settings')
      .then(v => setMode(parseBusyMode(v.ai_busy_mode)))
      .catch(() => {});
  }, []);

  useEffect(() => {
    loadMode();
    const handler = () => { loadMode(); };
    window.addEventListener(SETTINGS_UPDATED_EVENT, handler);
    return () => window.removeEventListener(SETTINGS_UPDATED_EVENT, handler);
  }, [loadMode]);

  const value = useMemo<AiBusyCtx>(() => ({
    busy,
    label,
    mode,
    setBusy: (on, text) => {
      setBusyState(on);
      if (text) setLabel(text);
      else if (!on) setLabel('AI 处理中…');
    },
  }), [busy, label, mode]);

  return (
    <Ctx.Provider value={value}>
      {children}
      {busy && <AiBusyIndicator mode={mode} label={label} />}
    </Ctx.Provider>
  );
}

export function useAiBusy() {
  return useContext(Ctx);
}
