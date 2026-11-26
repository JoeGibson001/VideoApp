import { request, type APIRequestContext } from '@playwright/test'

const BASE = process.env.E2E_BASE_URL || 'http://localhost:8080'
const KEY = process.env.E2E_INGEST_KEY || 'e2e-ingest'

// Seeds the fixture videos (mounted into the app container under /media/*/e2e) via the ingest API.
export default async function globalSetup() {
  const ctx = await request.newContext({ baseURL: BASE })
  for (let attempt = 0; attempt < 60; attempt++) {
    try {
      const r = await ctx.get('/api/health')
      if (r.ok()) break
    } catch { /* not up yet */ }
    await new Promise((res) => setTimeout(res, 1000))
  }
  const kinds = [
    { file: 'portrait', w: 360, h: 640, audio: true },
    { file: 'landscape', w: 640, h: 360, audio: true },
    { file: 'silent', w: 360, h: 640, audio: false },
  ]
  const tagSets = [['drift', 'cars'], ['cats', 'pets'], ['cooking'], ['drift', 'night'], ['ski', 'snow'], ['cats']]
  for (let i = 0; i < 18; i++) {
    const k = kinds[i % kinds.length]
    const r = await ctx.post('/api/internal/ingest', {
      headers: { 'X-Ingest-Key': KEY },
      data: {
        source: { domain: 'e2e.test', name: 'E2E' },
        source_video_id: `e2e-${i}`,
        source_url: `https://e2e.test/v/${i}`,
        creator: { name: `Creator ${i % 4}`, handle: `@creator${i % 4}`, profile_url: `https://e2e.test/@creator${i % 4}` },
        title: `Test video ${i} (${k.file})`,
        description: 'Fixture video for E2E tests',
        tags: tagSets[i % tagSets.length],
        upload_date: '2026-11-01',
        file: {
          path: `videos/e2e/${k.file}.mp4`, original_filename: `${k.file}.mp4`, size_bytes: 10000, width: k.w, height: k.h,
          duration_s: 3, fps: 24, video_codec: 'h264', audio_codec: k.audio ? 'aac' : null, has_audio: k.audio, container: 'mp4',
        },
        poster_path: `posters/e2e/${k.file}.jpg`,
      },
    })
    if (!r.ok()) throw new Error(`seed failed: ${r.status()} ${await r.text()}`)
  }
  // Visitors see nothing by default; the anonymous-mode tests need them to see the fixtures.
  await setVisitorAccess(ctx, true)
  await ctx.dispose()
}

export async function setVisitorAccess(ctx: APIRequestContext, all: boolean) {
  const login = await ctx.post('/api/auth/login', {
    data: { username: 'admin', password: process.env.E2E_ADMIN_PASSWORD || 'e2e-admin' },
  })
  if (!login.ok()) throw new Error(`admin login failed: ${login.status()}`)
  const csrf = (await ctx.storageState()).cookies.find((c) => c.name === 'vs_csrf')?.value || ''
  const r = await ctx.put('/api/admin/visibility/anonymous', {
    headers: { 'X-CSRF-Token': csrf }, data: { all_sources: all, source_ids: [] },
  })
  if (!r.ok()) throw new Error(`visitor access failed: ${r.status()} ${await r.text()}`)
}
