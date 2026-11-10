// Run this function with Playwright's page; substitute __FIXTURE_URL__ first.
// The fixture is local-only and never attaches to Herdr or calls an AI service.
async page => {
    const context = await page.context().browser().newContext({
        httpCredentials: { username: 'image-test', password: 'local-only-test' },
        permissions: ['clipboard-read', 'clipboard-write'],
    });
    await context.addInitScript(() => {
        const RealWebSocket = window.WebSocket;
        window.WebSocket = class extends RealWebSocket {
            constructor(...args) {
                super(...args);
                window.__imageTestSocket = this;
            }
        };
    });
    const p = await context.newPage();
    const replies = [];
    const inputs = [];
    const errors = [];
    const imagePayloads = [];
    let clipboard;
    p.on('pageerror', error => errors.push(error.message));
    p.on('websocket', socket => {
        let imageChunks = [];
        socket.on('framereceived', ({ payload }) => {
            const text = payload.toString();
            if (text[0] === '3') {
                const reply = JSON.parse(text.slice(1));
                replies.push(reply);
                if (reply.status === 'complete') imagePayloads.push(Buffer.concat(imageChunks));
            }
        });
        socket.on('framesent', ({ payload }) => {
            const text = payload.toString();
            if (text[0] === '0') inputs.push(text.slice(1));
            const bytes = Buffer.from(payload);
            if (bytes[0] === 52) imageChunks = []; // begin
            if (bytes[0] === 53) imageChunks.push(bytes.subarray(1));
        });
    });
    const assert = (condition, message) => {
        if (!condition) throw new Error(message);
    };
    const waitUntil = async condition => {
        for (let i = 0; i < 200; i++) {
            if (condition()) return;
            await p.waitForTimeout(50);
        }
        throw new Error('Timed out waiting for upload response');
    };
    const terminalText = () => p.evaluate(() => {
        const buffer = window.term.buffer.active;
        return Array.from({ length: buffer.length }, (_, i) => buffer.getLine(i)?.translateToString()).join('\n');
    });
    try {
        await p.goto('__FIXTURE_URL__');
        await p.waitForFunction(() => {
            const buffer = window.term?.buffer.active;
            return buffer && Array.from({ length: buffer.length }, (_, i) => buffer.getLine(i)?.translateToString()).join('').includes('BROWSER-PTY-READY');
        });
        await p.locator('.ttyd-image-upload:not([hidden])').waitFor();
        // Preserve the desktop clipboard without printing or recording its contents.
        clipboard = await p.evaluate(async () => {
            try {
                const items = await navigator.clipboard.read();
                window.__savedClipboard = items;
                return true;
            } catch {
                return false;
            }
        });
        await p.evaluate(async () => {
            const canvas = document.createElement('canvas');
            canvas.width = 640;
            canvas.height = 240;
            const c = canvas.getContext('2d');
            c.fillStyle = '#fff'; c.fillRect(0, 0, 640, 240);
            c.fillStyle = '#d11'; c.fillRect(20, 20, 100, 100);
            c.fillStyle = '#16c'; c.fillRect(140, 20, 100, 100);
            c.fillStyle = '#111'; c.font = 'bold 44px sans-serif';
            c.fillText('IMAGE TEST 7KQ3', 20, 195);
            const blob = await new Promise(resolve => canvas.toBlob(resolve));
            window.__imageTestBlob = blob;
            await navigator.clipboard.write([new ClipboardItem({ 'image/png': blob })]);
            document.addEventListener('paste', event => {
                window.__lastImagePasteTrusted = event.isTrusted;
                window.__lastImagePasteTypes = [...event.clipboardData.types];
            }, true);
            window.term.focus();
        });
        const isMac = await p.evaluate(() => /Mac/.test(navigator.platform));
        const pasteKey = isMac ? 'Meta+V' : 'Control+V';
        await p.keyboard.press(pasteKey);
        await waitUntil(() => replies.some(reply => reply.status === 'complete'));
        await waitUntil(() => inputs.length > 0);
        const first = replies.find(reply => reply.status === 'complete');
        const firstInput = inputs.join('');
        assert(firstInput.includes('image.png'), 'Image path was not pasted');
        assert(!/[\r\n]/.test(firstInput), 'Image paste must not synthesize Enter');
        assert(firstInput === `\x1b[200~${first.path}\x1b[201~`, 'Image paste must contain exactly the raw path inside bracketed-paste markers');
        const trustedPaste = await p.evaluate(() => window.__lastImagePasteTrusted);
        assert(trustedPaste === true, 'Expected a real browser clipboard paste event');

        const start = inputs.length;
        await p.evaluate(async () => {
            await navigator.clipboard.writeText('NORMAL-TEXT-PASTE');
            window.term.focus();
        });
        await p.keyboard.press(pasteKey);
        await waitUntil(() => inputs.slice(start).join('').includes('NORMAL-TEXT-PASTE'));
        assert(replies.filter(reply => reply.status === 'complete').length === 1, 'Text paste initiated an image upload');

        // The release dependency's compression regression also broke long text.
        const longStart = inputs.length;
        const longText = 'LONG-PASTE-' + 'x'.repeat(65536) + '-END';
        await p.evaluate(async text => {
            await navigator.clipboard.writeText(text);
            window.term.focus();
        }, longText);
        await p.keyboard.press(pasteKey);
        await waitUntil(() => inputs.slice(longStart).join('').includes('-END'));
        assert(inputs.slice(longStart).join('') === `\x1b[200~${longText}\x1b[201~`, 'Long clipboard text was truncated or changed');

        // Ctrl+V on macOS uses the browser clipboard instead of the remote one.
        await p.evaluate(async () => {
            await navigator.clipboard.write([new ClipboardItem({ 'image/png': window.__imageTestBlob })]);
            window.term.focus();
        });
        await p.keyboard.press('Control+V');
        await waitUntil(() => replies.filter(reply => reply.status === 'complete').length === 2);

        // Use a PNG larger than one 64 KiB upload chunk, with byte-for-byte
        // verification by the native runner after the browser finishes.
        const largePng = await p.evaluate(async () => {
            const canvas = document.createElement('canvas');
            canvas.width = canvas.height = 256;
            const c = canvas.getContext('2d');
            const pixels = c.createImageData(256, 256);
            for (let offset = 0; offset < pixels.data.length; offset += 65536) {
                crypto.getRandomValues(pixels.data.subarray(offset, offset + 65536));
            }
            for (let i = 3; i < pixels.data.length; i += 4) pixels.data[i] = 255;
            c.putImageData(pixels, 0, 0);
            window.__largeImageTestBlob = await new Promise(resolve => canvas.toBlob(resolve));
            return Array.from(new Uint8Array(await window.__largeImageTestBlob.arrayBuffer()));
        });
        assert(largePng.length > 65536, 'Fixture must exercise multiple upload chunks');
        // File chooser is also available when a mobile clipboard exposes no image.
        await p.locator('input[type="file"][accept*="image/png"]').setInputFiles({
            name: 'fixture.png', mimeType: 'image/png',
            buffer: Buffer.from(largePng),
        });
        await waitUntil(() => replies.filter(reply => reply.status === 'complete').length === 3);

        const inputBeforeFailure = inputs.length;
        await p.evaluate(() => {
            const data = new DataTransfer();
            data.items.add(new File(['not-a-png'], 'bad.png', { type: 'image/png' }));
            window.term.textarea.dispatchEvent(new ClipboardEvent('paste', { clipboardData: data, bubbles: true, cancelable: true }));
        });
        await waitUntil(() => replies.some(reply => reply.status === 'error'));
        assert(inputs.length === inputBeforeFailure, 'Failed upload injected terminal input');

        // A normal reconnect must reinstall image paste exactly once.
        await p.evaluate(() => window.__imageTestSocket.close(1000));
        await p.waitForFunction(() => document.body.textContent.includes('Press ⏎ to Reconnect'));
        await p.evaluate(() => window.term.focus());
        await p.keyboard.press('Enter');
        await p.locator('.ttyd-image-upload:not([hidden])').waitFor();
        await p.waitForFunction(() => {
            const b = window.term.buffer.active;
            return Array.from({ length: b.length }, (_, i) => b.getLine(i)?.translateToString()).join('').includes('BROWSER-PTY-READY');
        });
        assert(await p.locator('.ttyd-image-upload').count() === 1, 'Reconnect duplicated the image button');
        await p.evaluate(async () => {
            await navigator.clipboard.write([new ClipboardItem({ 'image/png': window.__imageTestBlob })]);
            window.term.focus();
        });
        await p.keyboard.press(pasteKey);
        await waitUntil(() => replies.filter(reply => reply.status === 'complete').length === 4);

        // Permission prompts/clipboard reads may outlive a connection. Never
        // deliver old clipboard text into a newly reconnected terminal.
        if (isMac) {
            await p.evaluate(() => {
                window.__normalClipboardRead = navigator.clipboard.read.bind(navigator.clipboard);
                Object.defineProperty(navigator.clipboard, 'read', {
                    configurable: true,
                    value: () => new Promise(resolve => { window.__resolveOldClipboard = resolve; }),
                });
                window.term.focus();
            });
            await p.keyboard.press('Control+V');
            await p.waitForFunction(() => window.__resolveOldClipboard);
            await p.evaluate(() => window.__imageTestSocket.close(1000));
            await p.waitForFunction(() => document.body.textContent.includes('Press ⏎ to Reconnect'));
            await p.evaluate(() => window.term.focus());
            await p.keyboard.press('Enter');
            await p.locator('.ttyd-image-upload:not([hidden])').waitFor();
            await p.waitForFunction(() => {
                const b = window.term.buffer.active;
                return Array.from({ length: b.length }, (_, i) => b.getLine(i)?.translateToString()).join('').includes('BROWSER-PTY-READY');
            });
            await p.evaluate(() => {
                window.__resolveOldClipboard([{
                    types: ['text/plain'],
                    getType: async () => new Blob(['STALE-CLIPBOARD'], { type: 'text/plain' }),
                }]);
                Object.defineProperty(navigator.clipboard, 'read', { configurable: true, value: window.__normalClipboardRead });
            });
            await p.waitForTimeout(100);
            assert(!inputs.join('').includes('STALE-CLIPBOARD'), 'Old clipboard read leaked into the new connection');
        }
        await p.evaluate(() => window.term.paste('AFTER-RECONNECT'));
        await waitUntil(() => inputs.join('').includes('AFTER-RECONNECT'));
        await p.waitForFunction(() => {
            const b = window.term.buffer.active;
            return Array.from({ length: b.length }, (_, i) => b.getLine(i)?.translateToString()).join('').includes('INPUT:');
        });
        assert(errors.length === 0, errors.join('\n'));
        // Clipboard PNGs may be re-encoded by the OS. Compare the saved files
        // with the actual bytes the browser uploaded, not the original canvas.
        assert(imagePayloads[2].equals(Buffer.from(largePng)), 'File chooser changed PNG bytes');
        const imageHashes = await p.evaluate(async images => Promise.all(images.map(async bytes =>
            Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256', new Uint8Array(bytes))))
                .map(n => n.toString(16).padStart(2, '0')).join('')
        )), imagePayloads.map(bytes => Array.from(bytes)));
        const completed = replies.filter(reply => reply.status === 'complete');
        return {
            trustedPaste,
            paths: completed.map(reply => reply.path),
            imageFiles: completed.map((reply, i) => ({ path: reply.path, sha256: imageHashes[i] })),
            normalTextPaste: true,
            longTextPaste: true,
            multiChunkImageBytes: largePng.length,
            macCtrlV: isMac ? true : null,
            fileChooser: true,
            failedUploadNoInput: true,
            reconnect: true,
            staleClipboardAfterReconnectIgnored: isMac ? true : null,
            imagePasteNeverSubmitsEnter: true,
            rawImagePathBracketedPaste: true,
            terminalResponsive: (await terminalText()).includes('INPUT:'),
            pageErrors: errors,
        };
    } finally {
        if (clipboard) {
            await p.evaluate(async () => {
                try { await navigator.clipboard.write(window.__savedClipboard); } catch { /* unsupported original formats */ }
            }).catch(() => {});
        }
        await context.close();
    }
}
