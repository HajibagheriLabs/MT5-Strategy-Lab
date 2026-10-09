import { expect, test } from '@playwright/test'
import { FakeApi } from './fake-api'

const EA = `input double Lots = 0.1;   // Lot size
input int    Period = 12;  // Moving average period
void OnTick() {}
`

test.beforeEach(async ({ page }) => {
  await new FakeApi().install(page)
})

test('upload an EA, run it, and read the result', async ({ page }) => {
  await page.goto('/strategies')
  await page.locator('input[type=file]').setInputFiles({ name: 'Cross.mq5', mimeType: 'text/plain', buffer: Buffer.from(EA) })

  await expect(page.getByText('Cross.mq5 is ready')).toBeVisible()
  await expect(page).toHaveURL(/\/strategies\/ea0001$/)
  const panel = page.locator('.strategy-panel')
  await expect(panel.getByRole('heading', { name: 'Cross.mq5' })).toBeVisible()
  await expect(panel.getByText('Moving average period')).toBeVisible()

  await panel.getByRole('button', { name: 'New run' }).click()
  await expect(page).toHaveURL(/\/new\?strategy=ea0001$/)
  await expect(page.getByLabel('Symbol')).toHaveValue('EURUSD@')
  await expect(page.getByText('History for EURUSD@ covers 2024-01-02 to 2025-12-31')).toBeVisible()
  await page.getByLabel('Lot size').fill('0.2')
  await page.getByRole('button', { name: 'Start run' }).click()

  await expect(page).toHaveURL(/\/runs\/run-1$/)
  await expect(page.getByRole('heading', { name: 'Cross' })).toBeVisible()
  await expect(page.getByText('Strategy Tester', { exact: true }).first()).toBeVisible()
  await expect(page.locator('.headline__item').first()).toContainText('+989.59')
  await expect(page.getByRole('tab', { name: /Deals/ })).toHaveAttribute('aria-selected', 'true')
  await expect(page.getByRole('cell', { name: 'tp 1.10500' }).first()).toBeVisible()

  await page.getByRole('tab', { name: 'Metrics' }).click()
  await expect(page.getByRole('columnheader', { name: 'MetaTrader' }).first()).toBeVisible()

  await page.getByRole('tab', { name: 'MetaTrader report' }).click()
  const report = page.frameLocator('iframe[title="MetaTrader report"]')
  await expect(report.getByText('Total Net Profit 989.59')).toBeVisible()
  // The report is shown as written, but nothing in it may run.
  await expect(report.getByText('scripts ran')).toHaveCount(0)
})

test('choosing a deal moves the price chart to it', async ({ page }) => {
  const api = new FakeApi()
  await api.install(page)
  await page.goto('/strategies')
  await page.locator('input[type=file]').setInputFiles({ name: 'Cross.mq5', mimeType: 'text/plain', buffer: Buffer.from(EA) })
  await page.locator('.strategy-panel').getByRole('button', { name: 'New run' }).click()
  await page.getByRole('button', { name: 'Start run' }).click()
  await expect(page.locator('.headline__item').first()).toContainText('+989.59')
  await expect(page.getByRole('img', { name: /Price chart/ }).locator('canvas').first()).toBeVisible()
  await page.getByRole('row', { name: /2025-01-02 13:00:00/ }).click()
  await expect(page.getByRole('row', { name: /2025-01-02 13:00:00/ })).toHaveAttribute('aria-selected', 'true')
})

test('a compile error is listed with its file and line, and the strategy cannot run', async ({ page }) => {
  await page.goto('/strategies')
  await page.locator('input[type=file]').setInputFiles({ name: 'Broken.mq5', mimeType: 'text/plain', buffer: Buffer.from(`${EA}BROKEN\n`) })
  await expect(page.getByText('Broken.mq5 has 1 error')).toBeVisible()
  const panel = page.locator('.strategy-panel')
  await expect(panel.locator('.diagnostics__where')).toHaveText('Broken.mq5:12:5')
  await expect(panel.getByText("'BROKEN' - undeclared identifier")).toBeVisible()
  await expect(panel.getByRole('button', { name: 'New run' })).toBeDisabled()
})

test('a file that is not a strategy is refused with the reason', async ({ page }) => {
  await page.goto('/strategies')
  await page.locator('input[type=file]').setInputFiles({ name: 'notes.txt', mimeType: 'text/plain', buffer: Buffer.from('hello') })
  await expect(page.getByText('The upload was refused')).toBeVisible()
  await expect(page.getByText(/notes\.txt.*is not a strategy/)).toBeVisible()
})

test('a period outside the history is refused with what is available', async ({ page }) => {
  await page.goto('/new?strategy=py0001')
  await page.getByLabel('Start date').fill('2023-06-01')
  await page.getByRole('button', { name: 'Start run' }).click()
  await expect(page.getByText(/History for EURUSD@ covers 2024-01-02 to 2025-12-31/).last()).toBeVisible()
  await expect(page).toHaveURL(/\/new/)
})

test('a Python strategy states its fidelity before the run starts', async ({ page }) => {
  await page.goto('/new?strategy=py0001')
  const summary = page.getByRole('complementary', { name: 'Summary' })
  await expect(summary.getByText('Simulated, not run in the Strategy Tester')).toBeVisible()
  await expect(summary.getByText('Prices come from 1-minute bars.')).toBeVisible()
  await expect(page.getByText('Python simulator').first()).toBeVisible()
})
