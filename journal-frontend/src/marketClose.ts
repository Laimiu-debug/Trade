import { api } from './api';

const cache = new Map<string, number | null>();

export function primeCloseCache(code: string, day: string, close: number | null) {
  cache.set(`${code}:${day}`, close);
}

export async function fetchCloseOnDay(code: string, day: string): Promise<number | null> {
  const key = `${code}:${day}`;
  if (cache.has(key)) return cache.get(key)!;
  try {
    const r = await api.get<{ close: number | null }>(
      `/api/market/${encodeURIComponent(code)}/close?day=${encodeURIComponent(day)}`,
    );
    const close = r.close != null && r.close > 0 ? r.close : null;
    cache.set(key, close);
    return close;
  } catch {
    cache.set(key, null);
    return null;
  }
}
