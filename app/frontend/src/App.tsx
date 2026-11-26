import { lazy, Suspense, type ReactNode } from 'react'
import { NavLink, Navigate, Route, Routes, useLocation, useParams } from 'react-router-dom'
import { Spinner } from './components/common'
import { AccountIcon, CollectionsIcon, FeedIcon, LibraryIcon } from './components/Icons'
import Feed from './feed/Feed'
import Account from './pages/Account'
import CategoryPage from './pages/CategoryPage'
import CollectionPage from './pages/CollectionPage'
import Collections from './pages/Collections'
import Library from './pages/Library'
import Login from './pages/Login'
import SearchPage from './pages/SearchPage'
import { useAuth } from './state'
import type { FeedType } from './types'

const Admin = lazy(() => import('./admin/Admin'))

const FEED_TYPES: FeedType[] = ['tag', 'creator', 'source', 'category', 'collection']

function FilteredFeed() {
  const { type, id } = useParams()
  if (!type || !FEED_TYPES.includes(type as FeedType) || !id || !/^\d+$/.test(id)) return <Navigate to="/" replace />
  return <Feed key={`${type}-${id}`} type={type as FeedType} id={Number(id)} />
}

function BottomNav() {
  const loc = useLocation()
  const onFeed = loc.pathname === '/' || loc.pathname.startsWith('/feed/')
  const cls = ({ isActive }: { isActive: boolean }) => (isActive ? 'active' : '')
  return (
    <nav className={'nav' + (onFeed ? ' translucent' : '')} aria-label="Main">
      <NavLink to="/" className={() => (onFeed ? 'active' : '')}><FeedIcon />Feed</NavLink>
      <NavLink to="/library" className={cls}><LibraryIcon />Library</NavLink>
      <NavLink to="/collections" className={cls}><CollectionsIcon />Collections</NavLink>
      <NavLink to="/account" className={cls}><AccountIcon />Account</NavLink>
    </nav>
  )
}

function RequireAdmin({ children }: { children: ReactNode }) {
  const { user, loading } = useAuth()
  if (loading) return <Spinner />
  if (!user) return <Navigate to="/login?next=/admin" replace />
  if (!user.is_admin) return <div className="empty">Admin only</div>
  return <>{children}</>
}

export default function App() {
  const { loading } = useAuth()
  const loc = useLocation()
  const hideNav = loc.pathname === '/login'
  return (
    <div className="shell">
      {loading ? <Spinner /> : (
        <Routes>
          <Route path="/" element={<Feed key="main" type="main" />} />
          <Route path="/feed/:type/:id" element={<FilteredFeed />} />
          <Route path="/search" element={<SearchPage />} />
          <Route path="/library" element={<Library />} />
          <Route path="/library/category/:id" element={<CategoryPage />} />
          <Route path="/collections" element={<Collections />} />
          <Route path="/collections/:id" element={<CollectionPage />} />
          <Route path="/account" element={<Account />} />
          <Route path="/login" element={<Login />} />
          <Route path="/admin/*" element={
            <RequireAdmin><Suspense fallback={<Spinner />}><Admin /></Suspense></RequireAdmin>
          } />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      )}
      {hideNav ? null : <BottomNav />}
    </div>
  )
}
