import { useEffect, useState } from 'react'
import { api, connect } from './api'

type Attachment = { id: string; original_name: string }

function blocks(markdown: string) {
  const lines = markdown.split(/\r?\n/)
  const result: Array<{ kind: 'title' | 'section' | 'bullet' | 'body'; text: string }> = []
  let paragraph: string[] = []
  const flush = () => { if (paragraph.length) { result.push({ kind: 'body', text: paragraph.join('\n') }); paragraph = [] } }
  for (const line of lines) {
    if (line.startsWith('# ')) { flush(); result.push({ kind: 'title', text: line.slice(2) }) }
    else if (line.startsWith('## ')) { flush(); result.push({ kind: 'section', text: line.slice(3) }) }
    else if (line.startsWith('- ')) { flush(); result.push({ kind: 'bullet', text: line.slice(2) }) }
    else if (!line.trim()) flush()
    else paragraph.push(line)
  }
  flush()
  return result
}

export function PrintPreview() {
  const query = new URLSearchParams(location.search)
  const accountId = query.get('account') || ''
  const kind = query.get('kind') || ''
  const key = query.get('key') || ''
  const valid = /^[a-f0-9]{32}$/.test(accountId) && ['daily', 'weekly', 'monthly'].includes(kind) && /^[0-9W-]+$/.test(key)
  const [markdown, setMarkdown] = useState('')
  const [attachments, setAttachments] = useState<Attachment[]>([])
  const [error, setError] = useState('')
  useEffect(() => {
    if (!valid) { setError('打印参数无效'); return }
    let active = true
    ;(async () => {
      await connect()
      const response = await fetch(`/api/v1/accounts/${accountId}/exports/review/${kind}/${key}.md`, { credentials: 'same-origin' })
      if (!response.ok) throw new Error(response.status === 404 ? '没有已保存的复盘' : '打印内容读取失败')
      const content = await response.text()
      const images = kind === 'daily' ? await api<Attachment[]>(`/accounts/${accountId}/daily-reviews/${key}/attachments`) : []
      if (active) { setMarkdown(images.length ? content.replace('图片文件未包含在此 Markdown，请在 Trade 应用中打开原复盘查看。', '原始截图附于打印文末。') : content); setAttachments(images); document.title = `${key} 复盘打印` }
    })().catch(reason => { if (active) setError(reason instanceof Error ? reason.message : '打印内容读取失败') })
    return () => { active = false }
  }, [accountId, kind, key, valid])
  return <main className="print-preview"><div className="print-toolbar"><strong>复盘打印预览</strong><span>仅打印已保存的内容</span><button className="button primary" type="button" disabled={!markdown || Boolean(error)} onClick={() => window.print()}>打开浏览器打印</button></div>
    {error && <p className="alert error" role="alert">{error}</p>}
    {!error && !markdown && <p>正在读取复盘…</p>}
    {markdown && <article className="print-document">{blocks(markdown).map((block, index) => block.kind === 'title' ? <h1 key={index}>{block.text}</h1> : block.kind === 'section' ? <h2 key={index}>{block.text}</h2> : block.kind === 'bullet' ? <p className="print-bullet" key={index}>• {block.text}</p> : <p key={index}>{block.text}</p>)}
      {attachments.length > 0 && <section className="print-attachments"><h2>原始复盘截图</h2>{attachments.map(item => <figure key={item.id}><img src={`/api/v1/accounts/${accountId}/review-attachments/${item.id}/content`} alt={item.original_name} /><figcaption>{item.original_name}</figcaption></figure>)}</section>}
      <footer>Trade · 复盘记录 · {key}</footer></article>}
  </main>
}

export function printPreviewUrl(accountId: string, kind: string, key: string) {
  return `/rebuild.html?${new URLSearchParams({ print: 'review', account: accountId, kind, key })}`
}
