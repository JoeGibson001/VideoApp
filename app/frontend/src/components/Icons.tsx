// Inline SVG icons (stroke = currentColor), matching mockup-player.html.
import type { ReactNode } from 'react'
type P = { size?: number; filled?: boolean; className?: string }

const S = ({ size = 24, children, className, fill = 'none' }: { size?: number; children: ReactNode; className?: string; fill?: string }) => (
  <svg width={size} height={size} viewBox="0 0 24 24" fill={fill} stroke="currentColor" strokeWidth={2}
    strokeLinecap="round" strokeLinejoin="round" className={className} aria-hidden="true">{children}</svg>
)

export const Heart = ({ size, filled }: P) => (
  <S size={size} fill={filled ? 'currentColor' : 'none'}>
    <path d="M12 21s-7.5-4.6-9.5-9.3C1.2 8.5 3.3 5 6.8 5c2 0 3.5 1.1 5.2 3 1.7-1.9 3.2-3 5.2-3 3.5 0 5.6 3.5 4.3 6.7C19.5 16.4 12 21 12 21z" />
  </S>
)
export const AddList = ({ size }: P) => <S size={size}><path d="M4 6h11M4 11h11M4 16h7M18 13v8M14 17h8" /></S>
export const Muted = ({ size }: P) => <S size={size}><path d="M4 9h4l5-4v14l-5-4H4z" /><path d="M17 9l5 6M22 9l-5 6" /></S>
export const Sound = ({ size }: P) => <S size={size}><path d="M4 9h4l5-4v14l-5-4H4z" /><path d="M17 8.5a5 5 0 0 1 0 7M19.5 6a8.5 8.5 0 0 1 0 12" /></S>
export const NoSound = ({ size }: P) => <S size={size}><path d="M4 9h4l5-4v14l-5-4H4z" /><path d="M3 3l18 18" /></S>
export const ChevronUp = ({ size = 20 }: P) => <S size={size}><path d="M6 15l6-6 6 6" /></S>
export const ChevronLeft = ({ size = 22 }: P) => <S size={size}><path d="M15 18l-6-6 6-6" /></S>
export const ChevronRight = ({ size = 18 }: P) => <S size={size}><path d="M9 6l6 6-6 6" /></S>
export const Close = ({ size = 18 }: P) => <S size={size}><path d="M6 6l12 12M18 6L6 18" /></S>
export const Search = ({ size = 18 }: P) => <S size={size}><circle cx="11" cy="11" r="7" /><path d="M20 20l-3.5-3.5" /></S>
export const Play = ({ size = 34 }: P) => (
  <svg width={size} height={size} viewBox="0 0 24 24" fill="#fff" aria-hidden="true"><path d="M8 5v14l11-7z" /></svg>
)
export const ThumbUp = ({ size = 14 }: P) => (
  <svg width={size} height={size} viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">
    <path d="M2 21h4V9H2v12zm20-11c0-1.1-.9-2-2-2h-6.3l1-4.6v-.3c0-.4-.2-.8-.4-1.1L13.2 1 6.6 7.6C6.2 7.9 6 8.4 6 9v10c0 1.1.9 2 2 2h9c.8 0 1.5-.5 1.8-1.2l3-7.1c.1-.2.2-.5.2-.7v-2z" />
  </svg>
)
export const ThumbDown = ({ size = 14 }: P) => <span style={{ display: 'inline-flex', transform: 'rotate(180deg)' }}><ThumbUp size={size} /></span>
export const FeedIcon = ({ size = 22 }: P) => (
  <svg width={size} height={size} viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><path d="M8 5v14l11-7z" /></svg>
)
export const LibraryIcon = ({ size = 22 }: P) => <S size={size}><path d="M4 6h16M4 12h16M4 18h10" /></S>
export const CollectionsIcon = ({ size = 22 }: P) => <S size={size}><rect x="3" y="4" width="18" height="16" rx="2" /><path d="M3 9h18" /></S>
export const AccountIcon = ({ size = 22 }: P) => <S size={size}><circle cx="12" cy="8" r="4" /><path d="M4 21c1.5-4 4.5-6 8-6s6.5 2 8 6" /></S>
export const More = ({ size = 20 }: P) => (
  <svg width={size} height={size} viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">
    <circle cx="5" cy="12" r="2" /><circle cx="12" cy="12" r="2" /><circle cx="19" cy="12" r="2" />
  </svg>
)
export const Eye = ({ size = 16 }: P) => <S size={size}><path d="M1 12s4-7 11-7 11 7 11 7-4 7-11 7S1 12 1 12z" /><circle cx="12" cy="12" r="3" /></S>
export const Grip = ({ size = 18 }: P) => <S size={size}><path d="M9 6h.01M15 6h.01M9 12h.01M15 12h.01M9 18h.01M15 18h.01" /></S>
export const Plus = ({ size = 18 }: P) => <S size={size}><path d="M12 5v14M5 12h14" /></S>
export const Trash = ({ size = 18 }: P) => <S size={size}><path d="M4 7h16M9 7V4h6v3M6 7l1 13h10l1-13" /></S>
export const External = ({ size = 14 }: P) => <S size={size}><path d="M14 4h6v6M20 4l-9 9M18 14v6H4V6h6" /></S>
