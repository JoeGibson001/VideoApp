import { expect, test, type Page } from '@playwright/test'

const ADMIN_PW = process.env.E2E_ADMIN_PASSWORD || 'e2e-admin'

async function csrf(page: Page): Promise<string> {
  const cookies = await page.context().cookies()
  return cookies.find((c) => c.name === 'vs_csrf')?.value || ''
}

async function login(page: Page) {
  await page.goto('/login')
  await page.getByLabel('Username').fill('admin')
  await page.getByLabel('Password').fill(ADMIN_PW)
  await page.getByRole('button', { name: 'Log in' }).click()
  await page.waitForURL((u) => !u.pathname.startsWith('/login'))
  const me = await (await page.request.get('/api/auth/me')).json()
  expect(me.user?.username).toBe('admin')
}

/** Clear every admin rating and favorite so each test starts from a full main feed. */
async function resetState(page: Page) {
  const token = await csrf(page)
  const headers = { 'X-CSRF-Token': token }
  const cols = await (await page.request.get('/api/collections')).json()
  for (const c of cols.items) {
    const d = await (await page.request.get(`/api/collections/${c.id}`)).json()
    for (const v of d.videos) {
      if (c.system_type === 'favorites') await page.request.delete(`/api/videos/${v.id}/favorite`, { headers })
      if (c.system_type === 'liked') await page.request.delete(`/api/videos/${v.id}/rating`, { headers })
    }
  }
  // dislikes are not in any collection: clear ratings for all seeded videos
  const vids = await (await page.request.get('/api/admin/videos', { params: { page_size: 200 } })).json()
  for (const v of vids.items) await page.request.delete(`/api/videos/${v.id}/rating`, { headers })
}

async function activeId(page: Page): Promise<number> {
  const el = page.getByTestId('active-card')
  await expect(el).toBeVisible()
  return Number(await el.getAttribute('data-video-id'))
}

async function rating(page: Page, id: number): Promise<number | null> {
  const r = await page.request.get(`/api/videos/${id}`)
  return (await r.json()).my_rating
}

async function drag(page: Page, dx: number, dy: number) {
  const box = (await page.getByTestId('feed').boundingBox())!
  const x = box.x + box.width / 2
  const y = box.y + box.height * 0.45
  await page.mouse.move(x, y)
  await page.mouse.down()
  const steps = 12
  for (let i = 1; i <= steps; i++) {
    await page.mouse.move(x + (dx * i) / steps, y + (dy * i) / steps)
    await page.waitForTimeout(16)
  }
  await page.mouse.up()
}

async function waitIdChange(page: Page, from: number) {
  await expect.poll(async () => activeId(page)).not.toBe(from)
}

test.describe('feed gestures (logged in)', () => {
  test.beforeEach(async ({ page }) => {
    await login(page)
    await resetState(page)
    await page.goto('/')
    await activeId(page)
  })

  test('swipe right records +1 and shows the next video', async ({ page }) => {
    const first = await activeId(page)
    const width = page.viewportSize()!.width
    await drag(page, width * 0.6, 0)
    await waitIdChange(page, first)
    await expect.poll(() => rating(page, first)).toBe(1)
    const liked = (await (await page.request.get('/api/collections')).json()).items.find((c: { system_type: string }) => c.system_type === 'liked')
    const detail = await (await page.request.get(`/api/collections/${liked.id}`)).json()
    expect(detail.videos.map((v: { id: number }) => v.id)).toContain(first)
  })

  test('swipe left records -1', async ({ page }) => {
    const first = await activeId(page)
    await drag(page, -page.viewportSize()!.width * 0.6, 0)
    await waitIdChange(page, first)
    await expect.poll(() => rating(page, first)).toBe(-1)
  })

  test('short horizontal drag springs back without rating', async ({ page }) => {
    const first = await activeId(page)
    await drag(page, page.viewportSize()!.width * 0.1, 0)
    await page.waitForTimeout(500)
    expect(await activeId(page)).toBe(first)
    expect(await rating(page, first)).toBeNull()
  })

  test('swipe up skips without rating, swipe down returns showing the rating badge', async ({ page }) => {
    const first = await activeId(page)
    await drag(page, page.viewportSize()!.width * 0.6, 0) // like first
    await waitIdChange(page, first)
    const second = await activeId(page)
    await drag(page, 0, -250) // skip second
    await waitIdChange(page, second)
    expect(await rating(page, second)).toBeNull()
    await drag(page, 0, 250) // back to second
    await expect.poll(() => activeId(page)).toBe(second)
    await expect(page.getByTestId('rating-badge')).toHaveCount(0)
    await drag(page, 0, 250) // back to first, which was liked
    await expect.poll(() => activeId(page)).toBe(first)
    await expect(page.getByTestId('rating-badge')).toContainText('Liked')
  })

  test('heart favorites without scrolling or pausing, then right swipe likes', async ({ page }) => {
    const first = await activeId(page)
    const feed = page.getByTestId('feed')
    const indexBefore = await feed.getAttribute('data-index')
    await page.getByTestId('active-card').getByTestId('fav-btn').click()
    await expect(page.getByTestId('active-card').getByTestId('fav-btn')).toHaveAttribute('aria-pressed', 'true')
    await page.waitForTimeout(400)
    expect(await feed.getAttribute('data-index')).toBe(indexBefore)
    expect(await activeId(page)).toBe(first)
    await expect(page.getByTestId('active-card').locator('.playicon')).toHaveCount(0)
    const cols = (await (await page.request.get('/api/collections')).json()).items
    const fav = cols.find((c: { system_type: string }) => c.system_type === 'favorites')
    await expect.poll(async () => (await (await page.request.get(`/api/collections/${fav.id}`)).json()).videos.map((v: { id: number }) => v.id)).toContain(first)
    expect(await rating(page, first)).toBeNull()
    await drag(page, page.viewportSize()!.width * 0.6, 0)
    await waitIdChange(page, first)
    await expect.poll(() => rating(page, first)).toBe(1)
  })

  test('details sheet opens and the video keeps its place', async ({ page }) => {
    const first = await activeId(page)
    await page.getByTestId('active-card').getByRole('button', { name: 'Details' }).click()
    await expect(page.getByTestId('details-sheet')).toBeVisible()
    await expect(page.getByTestId('details-sheet')).toContainText('Dimensions')
    expect(await activeId(page)).toBe(first)
  })

  test('portrait and landscape videos are never stretched', async ({ page }) => {
    for (let i = 0; i < 3; i++) {
      const card = page.getByTestId('active-card')
      const video = card.locator('video')
      const fit = await video.evaluate((v) => getComputedStyle(v).objectFit)
      expect(fit).toBe('contain')
      const box = (await video.boundingBox())!
      const vp = page.viewportSize()!
      expect(Math.round(box.width)).toBe(vp.width)
      const id = await activeId(page)
      await drag(page, 0, -250)
      await waitIdChange(page, id)
    }
  })
})

test('anonymous swipes advance but are not saved', async ({ page }) => {
  await page.goto('/')
  const first = await activeId(page)
  await expect(page.getByText('to save your likes')).toBeVisible()
  await drag(page, page.viewportSize()!.width * 0.6, 0)
  await waitIdChange(page, first)
  await drag(page, 0, 250)
  await expect.poll(() => activeId(page)).toBe(first)
  await expect(page.getByTestId('rating-badge')).toContainText('Liked')
  await login(page)
  expect(await rating(page, first)).toBeNull()
})

test('search updates on each keystroke and is typo tolerant', async ({ page }) => {
  await login(page)
  await page.goto('/search')
  const input = page.getByTestId('search-input')
  await input.pressSequentially('drfit', { delay: 60 })
  await expect(page.getByTestId('search-group-tag')).toContainText('drift')
})
