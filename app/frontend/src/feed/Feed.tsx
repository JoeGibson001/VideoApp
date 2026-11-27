import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { del, errorMessage, get, put, queueRating } from '../api'
import { Avatar } from '../components/common'
import { Close, Search, ThumbDown, ThumbUp } from '../components/Icons'
import { WatchIndicator } from '../components/WatchSection'
import { anonRatings, useAuth, usePlayback, usePrefersReducedMotion, useToast } from '../state'
import type { CollectionSummary, FeedHeader, FeedType, VideoSummary, WatchStatus } from '../types'
import { CreatorSheet, DetailsSheet, LoginSheet, SaveSheet } from './Sheets'
import { VideoCard, type CardHandlers } from './VideoCard'

const WINDOW = 2 // render previous 2, current, next 2 (DESIGN.md 5.3)
const PAGE = 10
const AXIS_LOCK_PX = 10
const COMMIT_FRACTION = 0.3
const FLICK_VELOCITY = 0.6 // px per ms
const NAV_THRESHOLD_PX = 80

type SheetKind = null | 'details' | 'save' | 'creator' | 'login'

function readFlag(key: string): boolean {
  try { return sessionStorage.getItem(key) === '1' } catch { return false }
}
function writeFlag(key: string) {
  try { sessionStorage.setItem(key, '1') } catch { /* ignore */ }
}

export default function Feed({ type, id }: { type: FeedType; id?: number }) {
  const { user } = useAuth()
  const toast = useToast()
  const navigate = useNavigate()
  const { muted, setMuted } = usePlayback()
  const reducedMotion = usePrefersReducedMotion()

  const [items, setItems] = useState<VideoSummary[]>([])
  const [index, setIndex] = useState(0)
  const [exhausted, setExhausted] = useState(false)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [header, setHeader] = useState<FeedHeader | null>(null)
  const [loginRequired, setLoginRequired] = useState(false)
  const [unratedOnly, setUnratedOnly] = useState(false)
  const [ratings, setRatings] = useState<Record<number, 1 | -1 | null>>({})
  const [favs, setFavs] = useState<Record<number, boolean>>({})
  const [favPop, setFavPop] = useState<Record<number, number>>({})
  const autoplay = user?.settings?.autoplay !== false
  const [paused, setPaused] = useState(!autoplay)
  const [sheet, setSheet] = useState<SheetKind>(null)
  const [creatorSheetId, setCreatorSheetId] = useState<number | null>(null)
  const [bannerHidden, setBannerHidden] = useState(() => readFlag('vs_banner_dismissed'))
  const [watch, setWatch] = useState<WatchStatus | null>(null)

  const feedRef = useRef<HTMLDivElement>(null)
  const trackRef = useRef<HTMLDivElement>(null)
  const cardEls = useRef(new Map<number, HTMLDivElement>())
  const loadingRef = useRef(false)
  const busy = useRef(false) // animation in progress
  const sheetOpenRef = useRef(false)
  sheetOpenRef.current = sheet !== null
  const itemsRef = useRef(items)
  itemsRef.current = items
  const indexRef = useRef(index)
  indexRef.current = index

  const isFiltered = type !== 'main'
  const current = items[index] as VideoSummary | undefined
  const atEnd = index >= items.length && exhausted

  const ratingOf = useCallback((v: VideoSummary | undefined): 1 | -1 | null => {
    if (!v) return null
    if (!user) return anonRatings.get(v.id) ?? null
    return v.id in ratings ? ratings[v.id] : v.my_rating
  }, [ratings, user])
  const favOf = useCallback((v: VideoSummary) => (v.id in favs ? favs[v.id] : v.is_favorite), [favs])

  // ---- loading ------------------------------------------------------------------------------

  const loadMore = useCallback(async (reset = false) => {
    if (loadingRef.current) return
    loadingRef.current = true
    setLoading(true)
    try {
      const have = reset ? [] : itemsRef.current
      const r = await get<{ items: VideoSummary[]; header: FeedHeader | null; login_required?: boolean }>('/api/feed', {
        type, id, limit: PAGE, unrated_only: isFiltered && unratedOnly ? true : undefined,
        exclude: have.map((v) => v.id).join(',') || undefined,
      })
      setHeader(r.header)
      setLoginRequired(!!r.login_required)
      setError(null)
      const seen = new Set(have.map((v) => v.id))
      const fresh = r.items.filter((v) => !seen.has(v.id))
      setItems(reset ? fresh : [...have, ...fresh])
      if (r.items.length < PAGE) setExhausted(true)
    } catch (e) {
      setError(errorMessage(e))
    } finally {
      loadingRef.current = false
      setLoading(false)
    }
  }, [type, id, isFiltered, unratedOnly])

  useEffect(() => {
    setItems([])
    setIndex(0)
    setExhausted(false)
    void loadMore(true)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [type, id, unratedOnly])

  useEffect(() => {
    if (!exhausted && !loading && items.length && index >= items.length - 3) void loadMore()
  }, [index, items.length, exhausted, loading, loadMore])

  useEffect(() => {
    if (type === 'creator' && id && user?.is_admin) {
      get<WatchStatus>(`/api/admin/creators/${id}/watch`).then(setWatch).catch(() => setWatch({ state: 'offline', watches: [] }))
    }
  }, [type, id, user])

  // ---- track position -----------------------------------------------------------------------

  const setTrack = useCallback((idx: number, dy = 0, animate = false) => {
    const t = trackRef.current
    if (!t) return
    t.style.transition = animate && !reducedMotion ? 'transform .35s cubic-bezier(.2,.8,.2,1)' : 'none'
    t.style.transform = `translate3d(0, calc(${-idx * 100}% + ${dy}px), 0)`
  }, [reducedMotion])

  const animateNext = useRef(false)
  useLayoutEffect(() => {
    setTrack(index, 0, animateNext.current)
    animateNext.current = false
  }, [index, setTrack])

  useEffect(() => { setPaused(!autoplay) }, [index, autoplay])

  const go = useCallback((delta: number) => {
    const idx = indexRef.current
    const max = exhausted ? itemsRef.current.length : itemsRef.current.length - 1
    const next = idx + delta
    if (next < 0) { setTrack(idx, 0, true); toast('This is the first video'); return }
    if (next > max) { setTrack(idx, 0, true); return }
    animateNext.current = true
    setIndex(next)
  }, [exhausted, setTrack, toast])

  // ---- rating -------------------------------------------------------------------------------

  const resetCard = (el: HTMLElement | undefined, animate: boolean) => {
    if (!el) return
    el.style.transition = animate && !reducedMotion ? 'transform .3s cubic-bezier(.2,.8,.2,1.2)' : 'none'
    el.style.transform = ''
    el.querySelectorAll<HTMLElement>('.stamp').forEach((s) => { s.style.opacity = '0' })
  }

  const commitRate = useCallback((value: 1 | -1) => {
    const v = itemsRef.current[indexRef.current]
    if (!v || busy.current) return
    const el = cardEls.current.get(v.id)
    const prev = ratingOf(v)
    if (prev === value) {
      resetCard(el, true)
      toast(value > 0 ? 'Already liked' : 'Already disliked')
      return
    }
    busy.current = true
    if (user) {
      setRatings((r) => ({ ...r, [v.id]: value }))
      queueRating(v.id, value)
    } else {
      anonRatings.set(v.id, value)
      setRatings((r) => ({ ...r }))
    }
    navigator.vibrate?.(12)
    const liked = user ? (prev ? 'Changed to Like, added to Liked' : 'Liked, added to Liked') : 'Liked'
    const disliked = user && prev ? 'Changed to Dislike, removed from Liked' : 'Disliked'
    toast(value > 0 ? liked : disliked)
    if (el) {
      const stamp = el.querySelector<HTMLElement>(value > 0 ? '.stamp.like' : '.stamp.nope')
      if (stamp) stamp.style.opacity = '1'
      if (reducedMotion) {
        el.style.transition = 'none'
      } else {
        el.style.transition = 'transform .3s ease-in'
        el.style.transform = `translateX(${value * 140}%) rotate(${value * 25}deg)`
      }
    }
    window.setTimeout(() => {
      const idx = indexRef.current
      const max = exhausted ? itemsRef.current.length : itemsRef.current.length - 1
      if (idx + 1 <= max) {
        animateNext.current = true
        setIndex(idx + 1)
      }
      // Accept the next gesture straight away; the flung card is reset once it is off screen.
      busy.current = false
      window.setTimeout(() => resetCard(el, false), reducedMotion ? 0 : 380)
    }, reducedMotion ? 0 : 260)
  }, [exhausted, ratingOf, reducedMotion, toast, user])

  const clearRating = useCallback((v: VideoSummary) => {
    if (user) {
      setRatings((r) => ({ ...r, [v.id]: null }))
      queueRating(v.id, null)
      toast('Rating cleared, removed from Liked')
    } else {
      anonRatings.delete(v.id)
      setRatings((r) => ({ ...r }))
      toast('Rating cleared')
    }
  }, [toast, user])

  // ---- favorites / sheets ---------------------------------------------------------------------

  const toggleFavorite = useCallback(async () => {
    const v = itemsRef.current[indexRef.current]
    if (!v) return
    if (!user) { setSheet('login'); return }
    const next = !favOf(v)
    setFavs((f) => ({ ...f, [v.id]: next }))
    setFavPop((p) => ({ ...p, [v.id]: (p[v.id] || 0) + 1 }))
    toast(next ? 'Added to Favorites' : 'Removed from Favorites')
    try {
      if (next) await put(`/api/videos/${v.id}/favorite`)
      else await del(`/api/videos/${v.id}/favorite`)
    } catch (e) {
      setFavs((f) => ({ ...f, [v.id]: !next }))
      toast(errorMessage(e))
    }
  }, [favOf, toast, user])

  const openSave = useCallback(() => { if (!user) setSheet('login'); else setSheet('save') }, [user])
  const closeSheet = useCallback(() => setSheet(null), [])

  const handlers: CardHandlers = useMemo(() => ({
    onCreator: (v) => navigate(`/feed/creator/${v.creator.id}`),
    onTag: (tid) => navigate(`/feed/tag/${tid}`),
    onDetails: () => setSheet('details'),
    onFavorite: () => void toggleFavorite(),
    onSave: openSave,
    onMute: () => setMuted(!muted),
  }), [navigate, toggleFavorite, openSave, setMuted, muted])

  const onPlayBlocked = useCallback(() => setMuted(true), [setMuted])
  const register = useCallback((vid: number, el: HTMLDivElement | null) => {
    if (el) cardEls.current.set(vid, el)
    else cardEls.current.delete(vid)
  }, [])

  // ---- gestures -----------------------------------------------------------------------------

  useEffect(() => {
    const root = feedRef.current
    if (!root) return
    let active = false
    let axis: 'x' | 'y' | null = null
    let sx = 0, sy = 0, st = 0, dx = 0, dy = 0, pid = -1
    const width = () => root.clientWidth

    const down = (e: PointerEvent) => {
      if (sheetOpenRef.current || busy.current) return
      if (e.button !== 0 && e.pointerType === 'mouse') return
      const target = e.target as HTMLElement
      if (target.closest('button, a, input, label, select, .sheet, .scrim, .nav, .banner, .topbar, .deskrate')) return
      active = true
      axis = null
      sx = e.clientX; sy = e.clientY; st = performance.now(); dx = 0; dy = 0; pid = e.pointerId
    }
    const move = (e: PointerEvent) => {
      if (!active || e.pointerId !== pid) return
      dx = e.clientX - sx
      dy = e.clientY - sy
      if (!axis && Math.hypot(dx, dy) > AXIS_LOCK_PX) {
        axis = Math.abs(dx) > Math.abs(dy) ? 'x' : 'y'
        try { root.setPointerCapture(pid) } catch { /* ignore */ }
      }
      const v = itemsRef.current[indexRef.current]
      if (axis === 'x' && v) {
        const el = cardEls.current.get(v.id)
        if (!el) return
        el.style.transition = 'none'
        el.style.transform = reducedMotion ? `translateX(${dx}px)` : `translateX(${dx}px) rotate(${dx / 18}deg)`
        const p = Math.min(1, Math.abs(dx) / (width() * COMMIT_FRACTION))
        const like = el.querySelector<HTMLElement>('.stamp.like')
        const nope = el.querySelector<HTMLElement>('.stamp.nope')
        if (like) like.style.opacity = dx > 0 ? String(p) : '0'
        if (nope) nope.style.opacity = dx < 0 ? String(p) : '0'
      } else if (axis === 'y') {
        setTrack(indexRef.current, dy, false)
      }
    }
    const up = (e: PointerEvent) => {
      if (!active || e.pointerId !== pid) return
      active = false
      try { root.releasePointerCapture(pid) } catch { /* ignore */ }
      const dt = Math.max(performance.now() - st, 1)
      if (!axis) {
        if (dt < 500 && itemsRef.current[indexRef.current]) setPaused((p) => !p)
        return
      }
      if (axis === 'x') {
        const fast = Math.abs(dx) / dt > FLICK_VELOCITY && Math.abs(dx) > 40
        const v = itemsRef.current[indexRef.current]
        if (v && (Math.abs(dx) > width() * COMMIT_FRACTION || fast)) commitRate(dx > 0 ? 1 : -1)
        else if (v) resetCard(cardEls.current.get(v.id), true)
      } else {
        const fast = Math.abs(dy) / dt > FLICK_VELOCITY && Math.abs(dy) > 30
        if (dy < -NAV_THRESHOLD_PX || (fast && dy < 0)) go(1)
        else if (dy > NAV_THRESHOLD_PX || (fast && dy > 0)) go(-1)
        else setTrack(indexRef.current, 0, true)
      }
    }
    const cancel = (e: PointerEvent) => {
      if (!active || e.pointerId !== pid) return
      active = false
      const v = itemsRef.current[indexRef.current]
      if (v) resetCard(cardEls.current.get(v.id), true)
      setTrack(indexRef.current, 0, true)
    }
    let wheelLock = 0
    const wheel = (e: WheelEvent) => {
      if (sheetOpenRef.current) return
      e.preventDefault()
      const now = performance.now()
      if (now < wheelLock || Math.abs(e.deltaY) < 25) return
      wheelLock = now + 550
      go(e.deltaY > 0 ? 1 : -1)
    }
    root.addEventListener('pointerdown', down)
    window.addEventListener('pointermove', move)
    window.addEventListener('pointerup', up)
    window.addEventListener('pointercancel', cancel)
    root.addEventListener('wheel', wheel, { passive: false })
    return () => {
      root.removeEventListener('pointerdown', down)
      window.removeEventListener('pointermove', move)
      window.removeEventListener('pointerup', up)
      window.removeEventListener('pointercancel', cancel)
      root.removeEventListener('wheel', wheel)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [commitRate, go, setTrack, reducedMotion])


  // ---- keyboard -----------------------------------------------------------------------------

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const t = e.target as HTMLElement
      if (t && (t.tagName === 'INPUT' || t.tagName === 'TEXTAREA' || t.tagName === 'SELECT' || t.isContentEditable)) return
      if (e.metaKey || e.ctrlKey || e.altKey) return
      const k = e.key
      if (sheetOpenRef.current) {
        if (k === 'i' || k === 'I') { e.preventDefault(); setSheet(null) }
        return
      }
      if (k === 'ArrowRight') { e.preventDefault(); commitRate(1) }
      else if (k === 'ArrowLeft') { e.preventDefault(); commitRate(-1) }
      else if (k === 'ArrowDown') { e.preventDefault(); go(1) }
      else if (k === 'ArrowUp') { e.preventDefault(); go(-1) }
      else if (k === ' ') { e.preventDefault(); setPaused((p) => !p) }
      else if (k === 'm' || k === 'M') setMuted(!muted)
      else if (k === 'i' || k === 'I') { if (itemsRef.current[indexRef.current]) setSheet('details') }
      else if (k === 'f' || k === 'F') void toggleFavorite()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [commitRate, go, muted, setMuted, toggleFavorite])

  // ---- render -------------------------------------------------------------------------------

  const rating = ratingOf(current)
  const visible: { v: VideoSummary; i: number }[] = []
  for (let i = Math.max(0, index - WINDOW); i <= Math.min(items.length - 1, index + WINDOW); i++) {
    visible.push({ v: items[i], i })
  }

  const goLiked = async () => {
    try {
      const r = await get<{ items: CollectionSummary[] }>('/api/collections')
      const liked = r.items.find((c) => c.system_type === 'liked')
      if (liked) navigate(`/feed/collection/${liked.id}`)
    } catch (e) { toast(errorMessage(e)) }
  }

  const close = () => {
    if (window.history.length > 1) navigate(-1)
    else navigate('/')
  }

  return (
    <div className="feed" ref={feedRef} data-testid="feed" data-index={index}>
      <div className="track" ref={trackRef}>
        {visible.map(({ v, i }) => (
          <VideoCard key={v.id} video={v} position={i} active={i === index}
            preload={i === index + 1 || i === index ? 'auto' : 'metadata'} muted={muted}
            paused={i === index ? paused : true} favorite={favOf(v)} favPop={favPop[v.id] || 0}
            handlers={handlers} register={register} onPlayBlocked={onPlayBlocked} />
        ))}
        {(exhausted || error) && !loading ? (
          <div className="vcard" style={{ top: `${items.length * 100}%` }} data-testid={atEnd ? 'end-card' : undefined}>
            <div className="endcard">
              {error ? <><h2>Could not load videos</h2><p className="muted">{error}</p>
                <button className="btn primary" onClick={() => void loadMore(items.length === 0)}>Try again</button></> : loginRequired && !user ? (
                <div data-testid="login-required" style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 12 }}>
                  <img src="/icons/icon-192.png" alt="" width={72} height={72} style={{ borderRadius: 18 }} />
                  <h2>Log in to watch</h2>
                  <p className="muted">Tastebud is only available to members.</p>
                  <Link className="btn primary" to="/login">Log in</Link>
                </div>
              ) : (
                <>
                  <h2>{items.length ? "You're all caught up" : 'Nothing here yet'}</h2>
                  <p className="muted">{isFiltered ? 'There are no more videos in this feed.' : 'You have seen every new video. Explore more:'}</p>
                  <Link className="btn" to="/library?tab=tags">Browse tags</Link>
                  <Link className="btn" to="/library?tab=creators">Browse creators</Link>
                  {user ? <button className="btn primary" onClick={() => void goLiked()}>Play your Liked videos</button> : null}
                  {isFiltered ? <button className="btn" onClick={close}>Back</button> : null}
                </>
              )}
            </div>
          </div>
        ) : null}
      </div>

      {loading && !items.length ? <div className="endcard"><div className="spinner" /></div> : null}

      <div className="topbar">
        {header ? (
          <span className="pill" data-testid="feed-pill">
            {header.type === 'creator'
              ? <button className="pill" style={{ padding: 0, background: 'none' }} onClick={() => { setCreatorSheetId(header.id); setSheet('creator') }}>
                  <Avatar name={header.label} url={header.avatar_url} size={22} /><span className="label">{header.label}</span>
                </button>
              : <span className="label">{labelFor(header)}</span>}
            <button className="iconbtn plain" style={{ width: 28, height: 28 }} onClick={close} aria-label="Close filtered feed"><Close size={16} /></button>
          </span>
        ) : null}
        {header?.type === 'creator' && user?.is_admin && watch ? (
          <button className="pill watchpill-btn" style={{ padding: 0, background: 'none' }} onClick={() => { setCreatorSheetId(header.id); setSheet('creator') }}>
            <WatchIndicator state={watch.state} />
          </button>
        ) : null}
        {isFiltered && user ? (
          <button className={'pill toggle' + (unratedOnly ? ' on' : '')} onClick={() => setUnratedOnly((u) => !u)} aria-pressed={unratedOnly}>
            Unrated only
          </button>
        ) : null}
        {rating ? (
          <span className={'pill badge ' + (rating > 0 ? 'up' : 'down')} data-testid="rating-badge">
            {rating > 0 ? <ThumbUp /> : <ThumbDown />}{rating > 0 ? 'Liked' : 'Disliked'}
          </span>
        ) : null}
        <Link to="/search" className="iconbtn" aria-label="Search" style={{ marginLeft: 'auto' }}><Search /></Link>
      </div>

      {!user && !bannerHidden && !loginRequired ? (
        <div className="banner" role="note">
          <span><Link to="/login">Log in</Link> to save your likes</span>
          <button className="iconbtn plain" aria-label="Dismiss" onClick={() => { setBannerHidden(true); writeFlag('vs_banner_dismissed') }}><Close /></button>
        </div>
      ) : null}

      {current ? (
        <>
          <div className="deskrate left"><button className="btn nope" onClick={() => commitRate(-1)} aria-label="Dislike">Nope</button></div>
          <div className="deskrate right"><button className="btn like" onClick={() => commitRate(1)} aria-label="Like">Like</button></div>
        </>
      ) : null}
      <div className="sr-only" aria-live="polite">{current ? `${current.title} by ${current.creator.name}` : ''}</div>
      <div className="sr-only">
        <button onClick={() => commitRate(1)}>Like video</button>
        <button onClick={() => commitRate(-1)}>Dislike video</button>
        <button onClick={() => go(1)}>Next video</button>
        <button onClick={() => go(-1)}>Previous video</button>
      </div>

      <DetailsSheet open={sheet === 'details'} video={current} rating={rating} onClose={closeSheet}
        onClear={() => current && clearRating(current)}
        onSave={() => setSheet(user ? 'save' : 'login')}
        onCreator={(cid) => { setCreatorSheetId(cid); setSheet('creator') }}
        onTag={(tid) => navigate(`/feed/tag/${tid}`)} />
      <SaveSheet open={sheet === 'save'} video={current} liked={rating === 1} favorite={current ? favOf(current) : false}
        onToggleFavorite={() => void toggleFavorite()} onClose={closeSheet} />
      <CreatorSheet open={sheet === 'creator'} creatorId={creatorSheetId} onClose={closeSheet}
        onPlay={(cid) => { setSheet(null); navigate(`/feed/creator/${cid}`) }} />
      <LoginSheet open={sheet === 'login'} onClose={closeSheet} />
    </div>
  )
}

function labelFor(h: FeedHeader): string {
  const kind = { tag: 'Tag', creator: 'Creator', source: 'Source', category: 'Mood', collection: 'Collection', main: '' }[h.type]
  return `${kind}: ${h.label.replace(/^#/, '')}`
}
