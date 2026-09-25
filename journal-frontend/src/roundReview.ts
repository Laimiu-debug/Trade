import { api } from './api';

export function roundKey(code: string, startDate: string): string {
  return `${code}:${startDate}`;
}

export async function saveRoundSummary(code: string, startDate: string, reviewSummary: string): Promise<string> {
  const res = await api.put<{ review_summary: string }>(
    `/api/trades/rounds/${encodeURIComponent(code)}/${startDate}/summary`,
    { review_summary: reviewSummary },
  );
  return res.review_summary;
}

export async function aiRoundReview(code: string, startDate: string): Promise<string> {
  const res = await api.post<{ review_summary: string }>(
    `/api/trades/rounds/${encodeURIComponent(code)}/${startDate}/ai-review`,
  );
  return res.review_summary ?? '';
}

export function roundDisplaySummary(reviewSummary?: string, reviewSnippet?: string): string {
  return (reviewSummary || '').trim() || (reviewSnippet || '').trim();
}
