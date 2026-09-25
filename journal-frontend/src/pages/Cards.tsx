import { useCallback, useEffect, useState } from 'react';
import { api, type FlashCard } from '../api';
import { Empty, useToast } from '../components';

export default function Cards() {
  const toast = useToast();
  const [cards, setCards] = useState<FlashCard[]>([]);
  const [content, setContent] = useState('');
  const [tags, setTags] = useState('');
  const [filter, setFilter] = useState('');

  const reload = useCallback(() => {
    api.get<FlashCard[]>('/api/cards').then(setCards).catch(() => {});
  }, []);

  useEffect(reload, [reload]);

  const add = async () => {
    if (!content.trim()) { toast('写点什么再保存'); return; }
    await api.post('/api/cards', { content, tags });
    setContent('');
    setTags('');
    toast('闪记已保存');
    reload();
  };

  const allTags = Array.from(new Set(cards.flatMap(c => c.tags.split(',').filter(Boolean))));
  const shown = filter ? cards.filter(c => c.tags.split(',').includes(filter)) : cards;

  return (
    <div className="fade-in">
      <div className="page-head">
        <div>
          <h2 className="page-title">灵感闪记</h2>
          <div className="page-sub">顿悟稍纵即逝，记下来的才是你的</div>
        </div>
      </div>

      <div className="card" style={{ marginBottom: 18 }}>
        <textarea value={content} onChange={e => setContent(e.target.value)}
          placeholder="此刻的灵感、市场规律、对自己的忠告……"
          onKeyDown={e => { if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) add(); }} />
        <div className="row" style={{ marginTop: 10 }}>
          <input placeholder="标签，逗号分隔（如：情绪,仓位）" style={{ flex: 1 }} value={tags} onChange={e => setTags(e.target.value)} />
          <button className="primary" onClick={add}>记下（Ctrl+Enter）</button>
        </div>
      </div>

      {allTags.length > 0 && (
        <div className="row" style={{ marginBottom: 14 }}>
          <button className={filter === '' ? 'primary' : 'ghost'} onClick={() => setFilter('')}>全部</button>
          {allTags.map(t => (
            <button key={t} className={filter === t ? 'primary' : 'ghost'} onClick={() => setFilter(t)}>{t}</button>
          ))}
        </div>
      )}

      {shown.length === 0 ? <Empty text="还没有闪记" /> : (
        <div className="grid grid-2">
          {shown.map(c => (
            <div className="flash-card" key={c.id}>
              <div className="content">{c.content}</div>
              <div className="meta">
                <span>{c.tags.split(',').filter(Boolean).map(t => <span key={t} className="tag gold">{t}</span>)}</span>
                <span className="row" style={{ gap: 6 }}>
                  {c.created_at.slice(0, 10)}
                  <button className="danger-ghost" onClick={async () => { await api.del(`/api/cards/${c.id}`); reload(); }}>删除</button>
                </span>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
