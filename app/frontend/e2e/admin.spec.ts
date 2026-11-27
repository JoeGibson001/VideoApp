import { expect, test, type Page } from '@playwright/test'

async function login(page: Page) {
  await page.goto('/login')
  await page.getByLabel('Username').fill('admin')
  await page.getByLabel('Password').fill(process.env.E2E_ADMIN_PASSWORD || 'e2e-admin')
  await page.getByRole('button', { name: /log in/i }).click()
  await page.waitForURL((u) => !u.pathname.startsWith('/login'))
}

test('admin tag, creator and source lists sort like the library', async ({ page }) => {
  await login(page)
  await page.goto('/admin/tags')
  const firstTag = page.locator('table.table').last().locator('tbody tr td.title').first()
  await expect(firstTag).toHaveText(/^#cars/)
  await page.getByLabel('Sort').selectOption('most_videos')
  await expect(firstTag).toHaveText(/^#(drift|cats)/)
  await page.getByLabel('Sort').selectOption('name_desc')
  await expect(firstTag).toHaveText(/^#snow/)
  // the choice is remembered
  await page.reload()
  await expect(page.getByLabel('Sort')).toHaveValue('name_desc')

  await page.goto('/admin/creators')
  await page.getByLabel('Sort').selectOption('name_desc')
  await expect(page.locator('tbody tr td.title').first()).toHaveText(/^Creator 3/)

  await page.goto('/admin/sources')
  await expect(page.getByLabel('Sort')).toBeVisible()
  await expect(page.getByText(/score/).first()).toBeVisible()
})

test('a tag can be watched on a source from the admin Tags page', async ({ page }, info) => {
  // each device project uses its own tag, so the runs do not see each other's URL
  const tag = info.project.name === 'pixel' ? 'snow' : 'ski'
  await login(page)
  await page.goto('/admin/tags')
  await page.getByRole('cell', { name: new RegExp(`^#${tag}`) }).click()
  const panel = page.getByTestId('tag-watches')
  await expect(panel.getByText('Watch this tag')).toBeVisible()
  await panel.getByRole('button', { name: /E2E/ }).click()
  await expect(panel.getByText(/No URL/)).toBeVisible()
  await panel.getByRole('textbox').fill(`https://e2e.test/tags/${tag}`)
  await panel.getByRole('button', { name: 'Save' }).click()
  await expect(panel.getByRole('link', { name: new RegExp(`e2e\\.test/tags/${tag}`) })).toBeVisible()
  // the watch controls appear once the tag has a page URL (the importer is not running in E2E)
  await expect(panel.getByText(/checked once a day|Importer|offline/i).first()).toBeVisible()
})

test('categories are called Moods', async ({ page }) => {
  await login(page)
  await page.goto('/library')
  await expect(page.getByRole('tab', { name: 'Moods' })).toBeVisible()
  await page.getByRole('tab', { name: 'Moods' }).click()
  await expect(page.getByPlaceholder('New mood name')).toBeVisible()
})
