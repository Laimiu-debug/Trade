import { useCallback, useEffect, useRef } from 'react';

/** debounce 自动保存 */
export function useAutosave(
  enabled: boolean,
  save: () => Promise<void>,
  deps: unknown[],
  delayMs = 2000,
) {
  const saveRef = useRef(save);
  saveRef.current = save;
  const skipFirst = useRef(true);
  useEffect(() => {
    if (!enabled) return;
    if (skipFirst.current) {
      skipFirst.current = false;
      return;
    }
    const t = window.setTimeout(() => { void saveRef.current(); }, delayMs);
    return () => window.clearTimeout(t);
  }, deps); // eslint-disable-line react-hooks/exhaustive-deps
}

export function useDirtyGuard(dirty: boolean) {
  useEffect(() => {
    const handler = (e: BeforeUnloadEvent) => {
      if (!dirty) return;
      e.preventDefault();
      e.returnValue = '';
    };
    window.addEventListener('beforeunload', handler);
    return () => window.removeEventListener('beforeunload', handler);
  }, [dirty]);
}

export function confirmDiscard(
  dirty: boolean,
  message = '有未保存的修改，确定离开吗？',
): boolean {
  if (!dirty) return true;
  return window.confirm(message);
}

export function useDirtyFlag(snapshot: string, current: string): boolean {
  return snapshot !== '' && current !== snapshot;
}

export function useSnapshotRef(initial = '') {
  const ref = useRef(initial);
  const set = useCallback((v: string) => { ref.current = v; }, []);
  const get = useCallback(() => ref.current, []);
  return { set, get };
}
