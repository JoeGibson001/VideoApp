import { memo, useEffect, useRef, useState } from 'react'
import { Avatar } from '../components/common'
import { AddList, ChevronUp, Heart, Muted, NoSound, Play, Sound } from '../components/Icons'
import type { VideoSummary } from '../types'

export interface CardHandlers {
  onCreator: (v: VideoSummary) => void
  onTag: (tagId: number) => void
  onDetails: () => void
  onFavorite: () => void
  onSave: () => void
  onMute: () => void
}

interface Props {
  video: VideoSummary
  position: number // index in the feed
  active: boolean
  preload: 'auto' | 'metadata'
  muted: boolean
  paused: boolean
  favorite: boolean
  favPop: number
  handlers: CardHandlers
  register: (id: number, el: HTMLDivElement | null) => void
  onPlayBlocked: () => void
}

function VideoCardImpl({ video: v, position, active, preload, muted, paused, favorite, favPop, handlers, register, onPlayBlocked }: Props) {
  const videoRef = useRef<HTMLVideoElement>(null)
  const barRef = useRef<HTMLElement>(null)
  const [buffering, setBuffering] = useState(true)
  const [started, setStarted] = useState(false)

  useEffect(() => {
    const el = videoRef.current
    if (!el) return
    el.muted = muted
    if (active && !paused) {
      const p = el.play()
      if (p) {
        p.catch((err: DOMException) => {
          if (err?.name === 'NotAllowedError' && !el.muted) {
            // Browser blocked unmuted autoplay: fall back to muted.
            onPlayBlocked()
          }
        })
      }
    } else {
      el.pause()
    }
  }, [active, paused, muted, onPlayBlocked])

  useEffect(() => {
    const el = videoRef.current
    if (!el) return
    const onTime = () => {
      if (barRef.current && el.duration) barRef.current.style.width = `${(el.currentTime / el.duration) * 100}%`
    }
    const onWaiting = () => setBuffering(true)
    const onPlaying = () => { setBuffering(false); setStarted(true) }
    const onCanPlay = () => setBuffering(false)
    el.addEventListener('timeupdate', onTime)
    el.addEventListener('waiting', onWaiting)
    el.addEventListener('playing', onPlaying)
    el.addEventListener('canplay', onCanPlay)
    return () => {
      el.removeEventListener('timeupdate', onTime)
      el.removeEventListener('waiting', onWaiting)
      el.removeEventListener('playing', onPlaying)
      el.removeEventListener('canplay', onCanPlay)
    }
  }, [])

  const shown = v.tags.slice(0, 3)
  const extra = v.tags.length - shown.length

  return (
    <div className="vcard" ref={(el) => register(v.id, el)} data-video-id={v.id} data-testid={active ? 'active-card' : undefined}
      style={{ top: `${position * 100}%` }} aria-hidden={!active}>
      {v.poster_url && !started ? <img className="poster" src={v.poster_url} alt="" /> : null}
      <video
        ref={videoRef}
        src={v.video_url}
        poster={v.poster_url || undefined}
        preload={preload}
        loop
        playsInline
        muted={muted}
        disablePictureInPicture
        aria-label={v.title}
        data-width={v.width ?? undefined}
        data-height={v.height ?? undefined}
      />
      {active && paused ? <div className="playicon"><div><Play /></div></div> : null}
      {active && buffering && !paused ? <div className="buffering"><div className="spinner" /></div> : null}
      <div className="stamp like">LIKE</div>
      <div className="stamp nope">NOPE</div>
      <div className="shade" />
      <div className="info">
        <button className="creator-line" onClick={() => handlers.onCreator(v)} aria-label={`Open ${v.creator.name} feed`}>
          <Avatar name={v.creator.name} url={v.creator.avatar_url} size={40} />
          <span>
            <span className="cname">{v.creator.handle}</span>
            <span className="csrc" style={{ display: 'block' }}>{v.creator.name} &middot; {v.source.name}</span>
          </span>
        </button>
        <p className="vtitle">{v.title}</p>
        <div className="chips">
          {shown.map((t) => (
            <button key={t.id} className="chip" onClick={() => handlers.onTag(t.id)}>#{t.name}</button>
          ))}
          {extra > 0 ? <button className="chip" onClick={handlers.onDetails} aria-label={`${extra} more tags`}>+{extra}</button> : null}
          <button className="expand" aria-label="Details" onClick={handlers.onDetails}><ChevronUp /></button>
        </div>
      </div>
      <div className="rail">
        <button className={'rbtn' + (favorite ? ' on' : '')} onClick={handlers.onFavorite}
          aria-label={favorite ? 'Remove from Favorites' : 'Add to Favorites'} aria-pressed={favorite} data-testid="fav-btn">
          <span className={'circ' + (favPop ? ' pop' : '')} key={favPop}><Heart filled={favorite} /></span><span>Favorite</span>
        </button>
        <button className="rbtn" onClick={handlers.onSave} aria-label="Add to collection">
          <span className="circ"><AddList /></span><span>Save</span>
        </button>
        {v.has_audio ? (
          <button className="rbtn" onClick={handlers.onMute} aria-label={muted ? 'Unmute' : 'Mute'}>
            <span className="circ">{muted ? <Muted /> : <Sound />}</span><span>{muted ? 'Muted' : 'Sound'}</span>
          </button>
        ) : (
          <button className="rbtn disabled" aria-label="This video has no sound" aria-disabled="true">
            <span className="circ"><NoSound /></span><span>No sound</span>
          </button>
        )}
      </div>
      <div className="progress"><i ref={barRef} /></div>
    </div>
  )
}

export const VideoCard = memo(VideoCardImpl)
