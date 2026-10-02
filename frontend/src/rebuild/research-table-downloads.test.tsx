import { expect, it } from 'vitest'
import { render, screen } from '@testing-library/react'
import { PlateauTableDownload, ResearchTradeDownloads } from './research-table-downloads'

it('native historical downloads use persisted task IDs and plain read-only links', () => {
  render(<><ResearchTradeDownloads kind="single" id="single-id" /><ResearchTradeDownloads kind="portfolio" id="portfolio-id" /></>)
  expect(screen.getAllByRole('link').map(link => link.getAttribute('href'))).toEqual([
    '/api/v1/backtests/single-id/trades.csv', '/api/v1/backtests/single-id/trades.html',
    '/api/v1/research/portfolios/portfolio-id/trades.csv', '/api/v1/research/portfolios/portfolio-id/trades.html'])
  expect(screen.getAllByRole('link').every(link => link.hasAttribute('download'))).toBe(true)
})

it('old point trades bind the exact registered detail key; all-point Excel is independent of visible pagination', () => {
  render(<><ResearchTradeDownloads kind="legacy" id="old-id" detailKey="point.+_01" /><PlateauTableDownload id="old-id" legacy /><PlateauTableDownload id="new-id" /></>)
  expect(screen.getByRole('link', { name: '导出原成交 CSV' }).getAttribute('href')).toBe('/api/v1/research/legacy-reports/old-id/trades.csv?detail_key=point.%2B_01')
  expect(screen.getByRole('link', { name: '导出原平原全点 Excel' }).getAttribute('href')).toBe('/api/v1/research/legacy-reports/old-id/plateau.xlsx')
  expect(screen.getByRole('link', { name: '导出平原全点 Excel' }).getAttribute('href')).toBe('/api/v1/research/plateaus/new-id/export.xlsx')
})
