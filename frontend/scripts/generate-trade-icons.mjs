// Keep the app mark editable as SVG; render each Windows size directly from it.
import { readFile, writeFile } from 'node:fs/promises'
import { fileURLToPath } from 'node:url'
import { chromium } from '@playwright/test'

const publicDir = new URL('../public/', import.meta.url)
const svg = await readFile(new URL('trade-mark.svg', publicDir), 'utf8')
const browser = await chromium.launch({ channel: process.platform === 'win32' ? 'msedge' : undefined, headless: true })
try {
  const page = await browser.newPage({ deviceScaleFactor: 1 })
  await page.setContent(`<style>html,body{margin:0;background:transparent}svg{display:block;width:100vw;height:100vh}</style>${svg}`)
  async function render(size) {
    await page.setViewportSize({ width: size, height: size })
    return page.screenshot({ omitBackground: true })
  }
  await writeFile(new URL('trade-icon.png', publicDir), await render(512))
  await writeFile(new URL('trade-apple-touch-icon.png', publicDir), await render(180))

  // PNG-backed ICO frames are supported by modern Windows and PyInstaller.
  const sizes = [16, 24, 32, 48, 64, 128, 256]
  const frames = []
  for (const size of sizes) frames.push(await render(size))
  const directory = Buffer.alloc(6 + 16 * sizes.length)
  directory.writeUInt16LE(1, 2)
  directory.writeUInt16LE(sizes.length, 4)
  let offset = directory.length
  frames.forEach((frame, index) => {
    const entry = 6 + index * 16, size = sizes[index]
    directory[entry] = size === 256 ? 0 : size
    directory[entry + 1] = directory[entry]
    directory.writeUInt16LE(1, entry + 4)
    directory.writeUInt16LE(32, entry + 6)
    directory.writeUInt32LE(frame.length, entry + 8)
    directory.writeUInt32LE(offset, entry + 12)
    offset += frame.length
  })
  await writeFile(new URL('trade-icon.ico', publicDir), Buffer.concat([directory, ...frames]))
  console.log(`Generated Trade PNG and ICO (${sizes.join(', ')} px) in ${fileURLToPath(publicDir)}`)
} finally {
  await browser.close()
}
