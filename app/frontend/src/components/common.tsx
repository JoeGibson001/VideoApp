import { useEffect, type ReactNode } from 'react'
import type { LibraryRow } from '../types'
import { Eye } from './Icons'

const AVATAR_COLORS = ['#0ea5e9', '#10b981', '#f59e0b', '#8b5cf6', '#ef4444', '#14b8a6', '#ec4899', '#6366f1']

export function initials(name: string): string {
  const parts = name.replace(/^@/, '').split(/[\s_.-]+/).filter(Boolean)
  return (parts.slice(0, 2).map((p) => p[0]).join('') || '?').toUpperCase()
}

export function Avatar({ name, url, size = 40, square = false }: { name: string; url?: string | null; size?: number; square?: boolean }) {
  const color = AVATAR_COLORS[[...name].reduce((a, c) => a + c.charCodeAt(0), 0) % AVATAR_COLORS.length]
  const style = { width: size, height: size, fontSize: Math.round(size * 0.38), borderRadius: square ? 8 : '50%' }
  if (url) return <img className="avatar" src={url} alt="" style={style} loading="lazy" />
  return <div className="avatar" style={{ ...style, background: color }} aria-hidden="true">{initials(name)}</div>
}

export function Highlight({ text, spans }: { text: string; spans?: number[][] }) {
  if (!spans || !spans.length) return <>{text}</>
  const out: ReactNode[] = []
  let last = 0
  spans.forEach(([s, e], i) => {
    if (s > last) out.push(text.slice(last, s))
    out.push(<mark key={i}>{text.slice(s, e)}</mark>)
    last = e
  })
  if (last < text.length) out.push(text.slice(last))
  return <>{out}</>
}

export const scoreClass = (n: number | undefined | null) => (!n ? 'zero' : n > 0 ? 'pos' : 'neg')
export const signed = (n: number | undefined | null) => (n && n > 0 ? `+${n}` : `${n ?? 0}`)

export function ScoreBadge({ score }: { score?: number | null }) {
  return <b className={'score ' + scoreClass(score)}>{signed(score)}</b>
}

/** One row in Library lists and search results (DESIGN.md 6.1). */
export function ItemRow({ row, onClick, actions, showScore = true }: {
  row: LibraryRow; onClick?: () => void; actions?: ReactNode; showScore?: boolean
}) {
  const icon = row.type === 'creator'
    ? <Avatar name={row.name} url={row.avatar_url} size={40} />
    : row.type === 'source'
      ? <Avatar name={row.name} url={row.icon_url} size={40} square />
      : <div className="rowicon" aria-hidden="true">{row.type === 'tag' ? '#' : row.type === 'collection' ? '▦' : '◈'}</div>
  const sub: string[] = []
  if (row.type === 'creator' && row.handle) sub.push(row.handle)
  if (row.type === 'source' && row.domain) sub.push(row.domain)
  if (row.video_count !== undefined) sub.push(`${row.video_count} video${row.video_count === 1 ? '' : 's'}`)
  if (row.item_count !== undefined) sub.push(`${row.item_count} item${row.item_count === 1 ? '' : 's'}`)
  if (showScore && row.likes !== undefined) sub.push(`${row.likes} up / ${row.dislikes} down`)
  return (
    <div className="itemrow">
      <button className="itemrow-main" onClick={onClick}>
        {icon}
        <span className="itemrow-text">
          <span className="itemrow-name">
            {row.type === 'tag' ? '#' : ''}<Highlight text={row.name} spans={row.highlight} />
            {row.watched ? <span className="watch-eye" title="Watched"><Eye size={14} /></span> : null}
          </span>
          <small>{sub.join(' · ')}</small>
        </span>
        {showScore && row.score !== undefined ? <ScoreBadge score={row.score} /> : null}
      </button>
      {actions}
    </div>
  )
}

export function Sheet({ open, onClose, children, label }: { open: boolean; onClose: () => void; children: ReactNode; label: string }) {
  useEffect(() => {
    if (!open) return
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') { e.stopPropagation(); onClose() } }
    window.addEventListener('keydown', onKey, true)
    return () => window.removeEventListener('keydown', onKey, true)
  }, [open, onClose])
  return (
    <>
      <div className={'scrim' + (open ? ' show' : '')} onClick={onClose} />
      <div className={'sheet' + (open ? ' show' : '')} role="dialog" aria-modal="true" aria-label={label} aria-hidden={!open}>
        <div className="grab" />
        {open ? children : null}
      </div>
    </>
  )
}

export function Spinner() {
  return <div className="spinner" role="progressbar" aria-label="Loading" />
}

export function EmptyState({ children }: { children: ReactNode }) {
  return <div className="empty">{children}</div>
}

export function PageHeader({ title, left, right }: { title: ReactNode; left?: ReactNode; right?: ReactNode }) {
  return (
    <header className="pagehead">
      <div className="pagehead-side">{left}</div>
      <h1>{title}</h1>
      <div className="pagehead-side right">{right}</div>
    </header>
  )
}

export function formatBytes(n?: number | null): string {
  if (!n && n !== 0) return '-'
  const u = ['B', 'KB', 'MB', 'GB', 'TB']
  let i = 0
  let v = n
  while (v >= 1024 && i < u.length - 1) { v /= 1024; i++ }
  return `${v.toFixed(v >= 10 || i === 0 ? 0 : 1)} ${u[i]}`
}

export function formatDuration(s?: number | null): string {
  if (!s && s !== 0) return '-'
  const t = Math.round(s)
  const h = Math.floor(t / 3600)
  const m = Math.floor((t % 3600) / 60)
  const sec = t % 60
  return h ? `${h}:${String(m).padStart(2, '0')}:${String(sec).padStart(2, '0')}` : `${m}:${String(sec).padStart(2, '0')}`
}

export function formatDate(iso?: string | null): string {
  if (!iso) return '-'
  const d = new Date(iso.length === 10 ? iso + 'T00:00:00' : iso)
  if (isNaN(d.getTime())) return iso
  return d.toLocaleDateString(undefined, { year: 'numeric', month: 'short', day: 'numeric' })
}

export function formatDateTime(iso?: string | null): string {
  if (!iso) return '-'
  const d = new Date(iso)
  if (isNaN(d.getTime())) return iso
  return d.toLocaleString(undefined, { year: 'numeric', month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' })
}

export function aspectLabel(w?: number | null, h?: number | null): string {
  if (!w || !h) return '-'
  const g = (a: number, b: number): number => (b ? g(b, a % b) : a)
  const d = g(w, h)
  const r = `${w / d}:${h / d}`
  const label = w < h ? 'portrait' : w > h ? 'landscape' : 'square'
  return `${r.length > 7 ? (w / h).toFixed(2) : r} (${label})`
}
