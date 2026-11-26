import { NavLink, Navigate, Route, Routes, useNavigate } from 'react-router-dom'
import { PageHeader } from '../components/common'
import { ChevronLeft } from '../components/Icons'
import './admin.css'
import Creators from './Creators'
import Import from './Import'
import Maintenance from './Maintenance'
import Merges from './Merges'
import Sources from './Sources'
import Tags from './Tags'
import Videos from './Videos'
import Watches from './Watches'

const TABS: [string, string][] = [
  ['videos', 'Videos'], ['creators', 'Creators'], ['tags', 'Tags'], ['sources', 'Sources'],
  ['import', 'Import'], ['watches', 'Watches'], ['merges', 'Merges'], ['maintenance', 'Maintenance'],
]

/** Admin area (DESIGN.md 9). Mounted at /admin/* behind the admin guard in App.tsx. */
export default function Admin() {
  const nav = useNavigate()
  return (
    <div className="page">
      <PageHeader title="Admin" left={
        <button className="iconbtn plain" aria-label="Back to account" onClick={() => nav('/account')}><ChevronLeft /></button>
      } />
      <div className="page-inner" style={{ maxWidth: 1100 }}>
        <nav className="admin-tabs" aria-label="Admin sections">
          {TABS.map(([path, label]) => (
            <NavLink key={path} to={`/admin/${path}`} className={({ isActive }) => (isActive ? 'active' : '')}>{label}</NavLink>
          ))}
        </nav>
        <Routes>
          <Route index element={<Navigate to="videos" replace />} />
          <Route path="videos" element={<Videos />} />
          <Route path="creators" element={<Creators />} />
          <Route path="tags" element={<Tags />} />
          <Route path="sources" element={<Sources />} />
          <Route path="import" element={<Import />} />
          <Route path="watches" element={<Watches />} />
          <Route path="merges" element={<Merges />} />
          <Route path="maintenance" element={<Maintenance />} />
          <Route path="*" element={<Navigate to="videos" replace />} />
        </Routes>
      </div>
    </div>
  )
}
