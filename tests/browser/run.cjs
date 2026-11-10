// Called by scripts/release/test-browser.py against its loopback-only fixture.
const fs = require('node:fs');
const path = require('node:path');
const assert = require('node:assert/strict');
const { chromium } = require('playwright');

(async () => {
    const [url, output, channel] = process.argv.slice(2);
    const browser = await chromium.launch({ headless: true, ...(channel ? { channel } : {}) });
    const base = await browser.newContext();
    const page = await base.newPage();
    try {
        const source = fs.readFileSync(path.join(__dirname, '../test_image_paste_browser.js'), 'utf8')
            .replaceAll('__FIXTURE_URL__', url);
        const runPaste = new Function(`return (${source});`)();
        const paste = await runPaste(page);
        const profiles = [
            { name: 'desktop', ua: 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)', platform: 'MacIntel', touch: 0, size: 13 },
            { name: 'iPhone', ua: 'Mozilla/5.0 (iPhone; CPU iPhone OS 18_7 like Mac OS X)', platform: 'iPhone', touch: 5, size: 11 },
            { name: 'iPad-desktop-mode', ua: 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)', platform: 'MacIntel', touch: 5, size: 11 },
            { name: 'Android', ua: 'Mozilla/5.0 (Linux; Android 15)', platform: 'Linux armv8l', touch: 5, size: 11 },
            { name: 'Windows-touch-desktop', ua: 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)', platform: 'Win32', touch: 5, size: 13 },
            { name: 'iPhone-explicit-font', ua: 'Mozilla/5.0 (iPhone; CPU iPhone OS 18_7 like Mac OS X)', platform: 'iPhone', touch: 5, size: 17, query: '?fontSize=17' },
        ];
        const fonts = [];
        for (const profile of profiles) {
            const context = await browser.newContext({
                httpCredentials: { username: 'image-test', password: 'local-only-test' },
                userAgent: profile.ua,
                viewport: { width: 980, height: 700 },
            });
            try {
                await context.addInitScript(({ platform, touch }) => {
                    Object.defineProperty(navigator, 'platform', { get: () => platform });
                    Object.defineProperty(navigator, 'maxTouchPoints', { get: () => touch });
                }, profile);
                const p = await context.newPage();
                await p.goto(url + (profile.query || ''));
                await p.waitForFunction(() => window.term?.options.fontSize && document.querySelector('.ttyd-image-upload:not([hidden])'));
                const size = await p.evaluate(() => window.term.options.fontSize);
                assert.equal(size, profile.size, profile.name);
                fonts.push({ name: profile.name, size });
            } finally {
                await context.close();
            }
        }
        fs.writeFileSync(output, JSON.stringify({ paste, fonts }, null, 2));
        console.log('PASS: real browser clipboard, 64 KiB text, multi-chunk PNG, reconnect, and mobile fonts');
    } finally {
        await browser.close();
    }
})().catch(error => { console.error(error); process.exitCode = 1; });
