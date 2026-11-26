export type FeedType = 'main' | 'tag' | 'creator' | 'source' | 'category' | 'collection'

export interface SourceRef { id: number; name: string; domain: string; icon_url: string | null }
export interface CreatorRef {
  id: number; name: string; handle: string; profile_url: string | null; avatar_url: string | null; source_id: number
}
export interface TagRef { id: number; name: string; score?: number }

export interface VideoSummary {
  id: number
  title: string
  creator: CreatorRef
  source: SourceRef
  tags: TagRef[]
  video_url: string
  poster_url: string | null
  width: number | null
  height: number | null
  duration_s: number | null
  has_audio: boolean
  is_hidden: boolean
  my_rating: 1 | -1 | null
  is_favorite: boolean
}

export interface VideoDetail extends VideoSummary {
  description: string
  source_url: string | null
  source_video_id: string
  filename: string | null
  original_filename: string | null
  file_path: string
  size_bytes: number | null
  fps: number | null
  video_codec: string | null
  audio_codec: string | null
  container: string | null
  upload_date: string | null
  imported_at: string | null
  creator_score: number | null
  source_score: number | null
  collections: { id: number; name: string; system_type: string | null }[]
  rated_at?: string | null
}

export interface FeedHeader {
  type: FeedType; id: number; label: string; avatar_url?: string | null; system_type?: string | null
}

export type ItemType = 'tag' | 'creator' | 'source'

export interface LibraryRow {
  type: ItemType | 'collection' | 'category'
  id: number
  name: string
  video_count?: number
  item_count?: number
  created_at?: string | null
  handle?: string
  avatar_url?: string | null
  source_id?: number
  domain?: string
  icon_url?: string | null
  aliases?: string[]
  score?: number
  likes?: number
  dislikes?: number
  last_rated_at?: string | null
  highlight?: number[][]
  match_rank?: number
  watched?: boolean
  system_type?: string | null
}

export interface Paged<T> { items: T[]; total: number; page: number; page_size: number }

export interface User { id: number; username: string; is_admin: boolean; settings: Record<string, unknown> }

export interface CollectionSummary {
  id: number; name: string; system_type: 'favorites' | 'liked' | null; video_count: number; cover_url: string | null
  created_at: string; updated_at: string; contains_video?: boolean
}

export interface CategorySummary {
  id: number; name: string; description: string; item_count: number; score: number; created_at: string; updated_at: string
}

export interface CategoryDetail extends CategorySummary {
  items: { tag: (LibraryRow & { category_item_id: number })[]; creator: (LibraryRow & { category_item_id: number })[]; source: (LibraryRow & { category_item_id: number })[] }
}

export interface Watch {
  id: number; url: string; kind: string; name: string; source_domain: string; enabled: boolean; paused_at: string | null
  tags: string[]; filters: Record<string, unknown>; initial_mode: string; backfill_count?: number | null
  created_at: string; last_checked_at: string | null; last_success_at: string | null; last_new_count: number | null
  last_error: string | null; consecutive_failures: number; next_run_at: string | null
  creator_source_domain: string | null; creator_handle: string | null; imported_count?: number; erroring?: boolean
}

export interface WatchStatus { state: 'watching' | 'paused' | 'error' | 'none' | 'offline'; watches: Watch[] }

export interface SearchResponse {
  q: string
  groups: Partial<Record<'tag' | 'creator' | 'source' | 'collection' | 'category', { total: number; items: LibraryRow[] }>>
}
