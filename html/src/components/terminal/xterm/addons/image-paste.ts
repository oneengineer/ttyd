import type { IDisposable, Terminal } from '@xterm/xterm';

const BEGIN = '4';
const CHUNK = '5';
const FINISH = '6';
const CANCEL = '7';
const CHUNK_BYTES = 64 * 1024;
const IMAGE_TYPES = new Set(['image/png', 'image/jpeg', 'image/webp', 'image/gif']);

interface UploadReply {
    id: string;
    status: string;
    path?: string;
    error?: string;
}

interface PendingReply {
    id: string;
    resolve: (reply: UploadReply) => void;
    reject: (error: Error) => void;
    timer: number;
}

// A ttyd integration, not an xterm.js fork. Image data travels outside PTY input.
export class ImagePaste implements IDisposable {
    private enabled = false;
    private maxBytes = 0;
    private windows = false;
    private generation = 0;
    private sequence = 0;
    private pending?: PendingReply;
    private queue: Promise<void> = Promise.resolve();
    private button = document.createElement('button');
    private picker = document.createElement('input');

    constructor(
        private terminal: Terminal,
        private send: (command: string, data?: Uint8Array) => boolean,
        private notify: (message: string, timeout?: number) => void
    ) {
        this.button.className = 'ttyd-image-upload';
        this.button.type = 'button';
        this.button.textContent = '＋ Image';
        this.button.title = 'Upload an image, or paste a copied image into the terminal';
        this.button.hidden = true;
        this.button.addEventListener('click', this.chooseImage);
        this.picker.type = 'file';
        this.picker.accept = [...IMAGE_TYPES].join(',');
        this.picker.multiple = true;
        this.picker.hidden = true;
        this.picker.addEventListener('change', this.onFiles);
        terminal.element?.append(this.button, this.picker);
        terminal.element?.addEventListener('paste', this.onPaste, true);
        terminal.attachCustomKeyEventHandler(event => {
            if (!this.enabled || event.key.toLowerCase() !== 'v' || event.altKey) return true;
            if (!event.ctrlKey && !event.metaKey) return true;
            // Let the browser dispatch paste, rather than sending Ctrl+V to the
            // remote agent (which would read the remote machine's clipboard).
            if (/Mac|iPhone|iPad/.test(navigator.platform) && event.ctrlKey && !event.metaKey) {
                if (event.type === 'keydown') {
                    event.preventDefault();
                    void this.readClipboard();
                }
            }
            return false;
        });
    }

    setCapabilities(enabled: boolean, maxBytes: number, windows: boolean) {
        this.enabled = enabled;
        this.maxBytes = maxBytes;
        this.windows = windows;
        this.button.hidden = !enabled;
        this.button.disabled = !enabled;
    }

    receive(reply: UploadReply) {
        const pending = this.pending;
        if (!pending || pending.id !== reply.id) return;
        this.pending = undefined;
        window.clearTimeout(pending.timer);
        if (reply.status === 'error') pending.reject(new Error(reply.error || 'Image upload failed'));
        else pending.resolve(reply);
    }

    disconnect() {
        this.generation++;
        this.enabled = false;
        this.button.disabled = true;
        if (!this.pending) return;
        window.clearTimeout(this.pending.timer);
        this.pending.reject(new Error('Connection closed. Please paste the image again after reconnecting.'));
        this.pending = undefined;
    }

    dispose() {
        this.disconnect();
        this.terminal.element?.removeEventListener('paste', this.onPaste, true);
        this.button.removeEventListener('click', this.chooseImage);
        this.picker.removeEventListener('change', this.onFiles);
        this.button.remove();
        this.picker.remove();
        this.terminal.attachCustomKeyEventHandler(() => true);
    }

    private chooseImage = () => this.picker.click();

    private onFiles = () => {
        this.enqueue(Array.from(this.picker.files || []));
        this.picker.value = '';
    };

    private onPaste = (event: ClipboardEvent) => {
        if (!this.enabled || !event.clipboardData) return;
        const files = Array.from(event.clipboardData.items)
            .filter(item => item.kind === 'file' && item.type.startsWith('image/'))
            .map(item => item.getAsFile())
            .filter((file): file is File => file !== null);
        if (files.length === 0) return; // Preserve xterm.js's ordinary text paste.
        event.preventDefault();
        event.stopImmediatePropagation();
        this.enqueue(files);
    };

    private async readClipboard() {
        const generation = this.generation;
        try {
            const items = await navigator.clipboard.read();
            const files: Blob[] = [];
            let text = '';
            for (const item of items) {
                const type = item.types.find(type => IMAGE_TYPES.has(type));
                if (type) files.push(await item.getType(type));
                else if (item.types.includes('text/plain')) text += await (await item.getType('text/plain')).text();
            }
            if (!this.enabled || generation !== this.generation) return;
            if (files.length > 0) this.enqueue(files);
            else if (text) this.terminal.paste(text);
            else this.notify('No image on the clipboard. Use ＋ Image to choose a file.', 5000);
        } catch {
            this.notify('Use ⌘V / Paste, or ＋ Image to choose a file.', 5000);
        }
    }

    private enqueue(files: Blob[]) {
        const generation = this.generation;
        this.queue = this.queue
            .then(async () => {
                for (const file of files) {
                    if (!this.enabled || generation !== this.generation) throw new Error('Terminal is disconnected');
                    await this.upload(file, generation);
                }
            })
            .catch((error: Error) => {
                if (generation !== this.generation) return;
                console.warn('[ttyd] image paste:', error.message);
                this.notify(error.message, 5000);
            });
    }

    private request(id: string, command: string, data?: Uint8Array): Promise<UploadReply> {
        return new Promise((resolve, reject) => {
            const timer = window.setTimeout(() => {
                this.pending = undefined;
                this.send(CANCEL);
                reject(new Error('Image upload timed out. Please try again.'));
            }, 35000);
            this.pending = { id, resolve, reject, timer };
            if (this.send(command, data)) return;
            this.pending = undefined;
            window.clearTimeout(timer);
            reject(new Error('Terminal is disconnected'));
        });
    }

    private async upload(file: Blob, generation: number) {
        if (!IMAGE_TYPES.has(file.type) || file.size === 0) throw new Error('Choose a PNG, JPEG, WebP or GIF image.');
        if (file.size > this.maxBytes)
            throw new Error(`Image is too large (limit: ${this.maxBytes / 1024 / 1024} MiB).`);
        const id = `${Date.now().toString(36)}-${++this.sequence}`;
        const metadata = new TextEncoder().encode(JSON.stringify({ id, type: file.type, size: file.size }));
        this.notify('Uploading image…');
        try {
            const ready = await this.request(id, BEGIN, metadata);
            if (ready.status !== 'ready') throw new Error('Unexpected image upload response');
            for (let offset = 0; offset < file.size; offset += CHUNK_BYTES) {
                const chunk = new Uint8Array(await file.slice(offset, offset + CHUNK_BYTES).arrayBuffer());
                if (!this.enabled || generation !== this.generation) throw new Error('Terminal is disconnected');
                const result = await this.request(id, CHUNK, chunk);
                if (result.status !== 'chunk') throw new Error('Unexpected image upload response');
                this.notify(
                    `Uploading image… ${Math.round((Math.min(offset + chunk.length, file.size) / file.size) * 100)}%`
                );
            }
            const complete = await this.request(id, FINISH);
            if (complete.status !== 'complete' || !complete.path) throw new Error('Image upload did not return a path');
            if (!this.enabled || generation !== this.generation) throw new Error('Terminal is disconnected');
            const path = this.windows ? complete.path.replace(/\\/g, '/') : complete.path;
            // Claude recognizes an image path in its bracketed-paste handler.
            // Shell quoting turns it into ordinary prompt text, including paths
            // with spaces/apostrophes. Let xterm honor the application's paste
            // mode and send only the literal path; never submit Enter.
            this.terminal.paste(path);
            this.terminal.focus();
            this.notify('Image pasted', 1500);
        } catch (error) {
            if (generation === this.generation) this.send(CANCEL);
            throw error;
        }
    }
}
