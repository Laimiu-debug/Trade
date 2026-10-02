/** Comparison identity only: original ledger symbols and manifests stay intact. */
export function marketSymbolKey(raw: string | null | undefined): string | null {
  const value = (raw || '').trim().toLowerCase()
  if (!value) return null
  const matched = /^(?:(sh|sz|bj))?([0-9]{6})(?:\.(sh|sz|bj))?$/.exec(value)
  if (!matched) return `raw:${value.toUpperCase()}`
  const [, prefix, code, suffix] = matched
  if (prefix && suffix && prefix !== suffix) return null
  const marker = prefix || suffix
  // Explicit Shanghai and TDX sector indices are distinct from same-code stocks.
  if ((marker === 'sh' && code.startsWith('000')) || ((marker === 'sh' || marker === 'sz') && code.startsWith('88'))) return marker + code
  const stockExchange = /^(4|8|920)/.test(code) ? 'bj'
    : /^(600|601|603|605|688|689)/.test(code) ? 'sh'
      : /^(000|001|002|003|300|301)/.test(code) ? 'sz' : null
  if (stockExchange) return marker && marker !== stockExchange ? null : stockExchange + code
  const fallback = /^(5|6|9)/.test(code) ? 'sh' : /^(4|8)/.test(code) ? 'bj' : 'sz'
  return (marker || fallback) + code
}

export function sameMarketSymbol(left: string | null | undefined, right: string | null | undefined): boolean {
  const key = marketSymbolKey(left)
  return key !== null && key === marketSymbolKey(right)
}
