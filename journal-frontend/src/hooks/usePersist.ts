import { useCallback, useEffect, useRef } from 'react';

/** debounce 自动保存 */
export function useAutosave(
  enabled: boolean,
  save: () => Promise<void>,
  deps: unknown[],
  delayMs = 2000,
) {
  const pending = useRef<{ save: () => Promise<void>; timer: number } | null>(null);
  // This cleanup must precede the scheduling effect's cleanup: an internal
  // route change flushes its last committed editor, not a newer scope's save.
  useEffect(() => () => {
    const operation = pending.current;
    if (!operation) return;
    pending.current = null;
    window.clearTimeout(operation.timer);
    void operation.save().catch(() => { /* The editor reports save failures. */ });
  }, []);
  useEffect(() => {
    if (!enabled) return;
    const operation = { save, timer: 0 };
    operation.timer = window.setTimeout(() => {
      if (pending.current === operation) pending.current = null;
      void operation.save().catch(() => { /* The editor reports save failures. */ });
    }, delayMs);
    pending.current = operation;
    return () => {
      window.clearTimeout(operation.timer);
      if (pending.current === operation) pending.current = null;
    };
  }, [enabled, delayMs, ...deps]); // eslint-disable-line react-hooks/exhaustive-deps
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
