// ==UserScript==
// @name         ttyd Mobile Controller
// @match        <all_urls>
// @description  Mobile Overlay: Virtual Ctrl/Tab/Esc/Arrow Keys, 1-finger smooth scroll, Voice Draft & Tmux Macros
// @author       djshigel
// @version      26.1
// @license      MIT
// @namespace https://greasyfork.org/users/1322886
// @downloadURL https://update.greasyfork.org/scripts/565192/ttyd%20Mobile%20Controller.user.js
// @updateURL https://update.greasyfork.org/scripts/565192/ttyd%20Mobile%20Controller.meta.js
// ==/UserScript==

(function() {
    'use strict';

    // --- User Configuration ---
    const LONG_PRESS_MS = 500;  // Right-click long press duration (ms)
    const DRAG_THRESHOLD = 15;  // Threshold to detect panel dragging (px)
    const SCROLL_GESTURE_THRESHOLD = 10; // px — below this, treat as tap (not scroll)
    const ARROW_SWIPE_SENSITIVITY = 30; // px per arrow key when ✥ mode is on
    const ENTER_DELAY_MS = 200; // Delay before sending Enter after text input (ms)

    // --- Global State ---
    let scrollModeActive = false;

    // 1. Startup Observer
    // Detects when the terminal is ready and injects the UI.
    const TARGET_SELECTOR = '#terminal-container';
    const observer = new MutationObserver((mutations, obs) => {
        const terminalContainer = document.querySelector(TARGET_SELECTOR);
        if (terminalContainer && (window.term || window.socket)) {
            console.log('â TTYD detected. Injecting UI v26.1...');
            initUI();
            initBackgroundControl(terminalContainer);
            obs.disconnect();
        }
    });
    observer.observe(document.body, { childList: true, subtree: true });

    // 2. Data Transmission
    // Sends sequences via WebSocket or xterm.js input handler.
    function sendData(sequence) {
        const encoder = new TextEncoder();
        if (window.socket && window.socket.readyState === 1) {
            window.socket.send(encoder.encode(sequence));
        } else if (window.term && window.term.input) {
            window.term.input(sequence);
        }
    }

    // 3. Trigger Mouse / Wheel Events
    // Dispatches native-like events on the terminal canvas (xterm handles wheel scroll).
    function getTerminalCanvas(target) {
        return target.querySelector('canvas') || target;
    }

    function fireMouseEvent(type, target, x, y, buttonCode) {
        const canvas = getTerminalCanvas(target);
        const opts = {
            bubbles: true, cancelable: true, view: window,
            button: buttonCode, buttons: (buttonCode === 2 ? 2 : 1),
            clientX: x, clientY: y
        };
        canvas.dispatchEvent(new MouseEvent(type, opts));
    }

    function fireWheelEvent(target, deltaX, deltaY, clientX, clientY) {
        const canvas = getTerminalCanvas(target);
        canvas.dispatchEvent(new WheelEvent('wheel', {
            bubbles: true, cancelable: true, view: window,
            clientX, clientY,
            deltaX, deltaY,
            deltaMode: WheelEvent.DOM_DELTA_PIXEL
        }));
    }

    // 4. Background Interaction
    // Default: 1-finger swipe → wheel scroll (xterm native-like).
    // ✥ mode on: 1-finger swipe → arrow keys; wheel scroll disabled.
    // 1-finger tap / long-press → native click / right-click.
    // 2+ fingers → untouched (native page scroll).
    function initBackgroundControl(target) {
        let startX = 0, startY = 0, lastX = 0, lastY = 0;
        let accX = 0, accY = 0;
        let longPressTimer = null;
        let isSwipeGesture = false;

        const shouldIgnoreTouch = (e) =>
            e.target.closest('#vibe-floater') ||
            (document.activeElement && document.activeElement.id === 'draft-input');

        const resetGesture = () => {
            clearTimeout(longPressTimer);
            longPressTimer = null;
            isSwipeGesture = false;
            accX = 0;
            accY = 0;
        };

        target.addEventListener('touchstart', (e) => {
            if (shouldIgnoreTouch(e)) return;
            if (e.touches.length !== 1) return;

            const touch = e.touches[0];
            startX = touch.clientX; startY = touch.clientY;
            lastX = startX; lastY = startY;
            accX = 0; accY = 0;
            isSwipeGesture = false;

            longPressTimer = setTimeout(() => {
                if (isSwipeGesture) return;
                if (navigator.vibrate) navigator.vibrate([40]);
                fireMouseEvent('mousedown', target, startX, startY, 2);
                setTimeout(() => fireMouseEvent('mouseup', target, startX, startY, 2), 50);
            }, LONG_PRESS_MS);
        }, { passive: true });

        target.addEventListener('touchmove', (e) => {
            if (shouldIgnoreTouch(e)) return;

            if (e.touches.length !== 1) {
                resetGesture();
                return;
            }

            const touch = e.touches[0];
            const dx = touch.clientX - startX;
            const dy = touch.clientY - startY;

            if (!isSwipeGesture) {
                if (Math.hypot(dx, dy) < SCROLL_GESTURE_THRESHOLD) return;
                isSwipeGesture = true;
                clearTimeout(longPressTimer);
                longPressTimer = null;
            }

            e.preventDefault();

            const deltaX = touch.clientX - lastX;
            const deltaY = touch.clientY - lastY;
            lastX = touch.clientX;
            lastY = touch.clientY;

            if (scrollModeActive) {
                // ✥ mode: virtual arrow keys (wheel scroll off)
                accX += deltaX;
                accY += deltaY;
                if (Math.abs(accX) >= ARROW_SWIPE_SENSITIVITY) {
                    if (accX > 0) { sendData('\x1b[C'); accX -= ARROW_SWIPE_SENSITIVITY; }
                    else { sendData('\x1b[D'); accX += ARROW_SWIPE_SENSITIVITY; }
                }
                if (Math.abs(accY) >= ARROW_SWIPE_SENSITIVITY) {
                    if (accY > 0) { sendData('\x1b[B'); accY -= ARROW_SWIPE_SENSITIVITY; }
                    else { sendData('\x1b[A'); accY += ARROW_SWIPE_SENSITIVITY; }
                }
            } else {
                // Default: smooth wheel scroll
                if (deltaX !== 0 || deltaY !== 0) {
                    fireWheelEvent(target, -deltaX, -deltaY, touch.clientX, touch.clientY);
                }
            }
        }, { passive: false });

        target.addEventListener('touchend', resetGesture, { passive: true });
        target.addEventListener('touchcancel', resetGesture, { passive: true });
    }

    // 5. UI Construction
    function initUI() {
        const style = document.createElement('style');
        style.innerHTML = `
            #vibe-floater {
                position: fixed; top: 10vh; right: 2vw;
                width: 76vw; min-width: 200px; max-width: 400px;
                background: rgba(10, 10, 10, 0.97);
                border: 1px solid rgba(255, 255, 255, 0.15);
                border-radius: 16px; padding: 6px; z-index: 2147483647;
                backdrop-filter: blur(20px); box-shadow: 0 15px 50px rgba(0, 0, 0, 0.8);
                touch-action: none; user-select: none; -webkit-user-select: none;
                display: flex; flex-direction: column; gap: 4px;
                transition: height 0.2s;
            }
            #vibe-floater.mode-ctrl { width: 95vw; max-width: 800px; }
            .layer-grid { display: grid; grid-template-columns: repeat(6, 1fr); gap: 5px; width: 100%; }
            #layer-ctrl { display: none; grid-template-columns: repeat(10, 1fr); gap: 3px; }

            .vb-button {
                background: rgba(255, 255, 255, 0.08); color: #eee; border-radius: 8px;
                font-family: monospace; font-weight: 700; display: flex;
                justify-content: center; align-items: center; box-shadow: 0 2px 4px rgba(0,0,0,0.4);
                grid-column: span 2; aspect-ratio: 5/3; font-size: clamp(16px, 5.5vw, 22px);
            }
            .vb-button.active-touch { background: rgba(255, 255, 255, 0.3); transform: scale(0.94); }

            .vb-button.row-ctrl { grid-column: span 3; height: 60px; aspect-ratio: auto; font-size: 18px; }
            .icon-lg { font-size: 36px !important; }

            /* Input Area */
            #draft-container {
                grid-column: span 6;
                display: flex; gap: 6px;
                margin-top: 2px;
            }
            #draft-input {
                flex: 1;
                height: 40px; /* Default height */
                background: rgba(40, 40, 40, 0.9);
                color: #fff;
                border: 1px solid rgba(255, 255, 255, 0.2);
                border-radius: 8px;
                font-family: monospace;
                font-size: 16px; /* Larger font for visibility and focus */
                padding: 10px;
                resize: none;
                transition: height 0.2s, background 0.2s;
            }
            #draft-input:focus {
                height: 100px; /* Expanded height on focus */
                background: rgba(0, 0, 0, 0.95);
                border-color: #00aaff;
                outline: none;
            }
            #draft-send-button {
                width: 50px; height: auto;
                background: #0077aa; color: white;
                border: none; border-radius: 8px;
                display: flex; justify-content: center; align-items: center;
                font-size: 24px;
                font-weight: bold;
                padding-bottom: 4px;
            }
            #draft-send-button:active { background: #0099cc; }

            #button-scroll-toggle.toggle-on { background: rgba(0, 150, 255, 0.5) !important; color: #fff; border: 1px solid #00aaff; }

            #layer-ctrl .vb-button { grid-column: span 1; height: 50px; font-size: 18px; background: rgba(50, 70, 90, 0.6); aspect-ratio: auto; }
            .vb-button[data-color="red"] { background: rgba(200, 50, 50, 0.35); }
            .vb-button[data-color="blue"] { background: rgba(30, 100, 200, 0.35); }
            .span-full { grid-column: 1 / -1; height: 50px; background: #333 !important; aspect-ratio: auto; margin-top: 5px; }
        `;
        document.head.appendChild(style);

        const floater = document.createElement('div');
        floater.id = 'vibe-floater';
        document.body.appendChild(floater);

        // --- Main Layer ---
        const mainLayer = document.createElement('div');
        mainLayer.id = 'layer-main';
        mainLayer.className = 'layer-grid';
        floater.appendChild(mainLayer);

        // Grid Layout (6 columns)
        const mainKeys = [
            // Row 1
            { label: 'Esc', seq: '\x1b' }, 
            { label: 'C-c', seq: '\x03', color: 'red' },
            { label: 'Ent', seq: '\r' },
            // Row 2
            { label: 'Tab', seq: '\t' },
            { label: '▲', seq: '\x1b[A' },
            { label: 'C-b', seq: '\x02', color: 'blue' },
            // Row 3
            { label: '◀', seq: '\x1b[D' },
            { label: '▼', seq: '\x1b[B' },
            { label: '▶', seq: '\x1b[C' },
            // Row 4
            { label: 'CTRL / MENU', action: 'showCtrl', cls: 'row-ctrl', color: 'blue' },
            { label: '✥', action: 'toggleScroll', cls: 'row-ctrl icon-lg', id: 'button-scroll-toggle' }
        ];

        mainKeys.forEach((k, i) => {
            const button = document.createElement('div');
            button.className = `vb-button ${k.cls || ''}`;
            if (k.id) button.id = k.id;
            button.textContent = k.label;
            if (k.color) button.dataset.color = k.color;
            if (k.action) button.dataset.action = k.action;
            button.dataset.layer = 'main';
            button.dataset.index = i;
            mainLayer.appendChild(button);
        });

        // --- Input Row (Always visible) ---
        const draftContainer = document.createElement('div');
        draftContainer.id = 'draft-container';
        draftContainer.innerHTML = `
            <textarea id="draft-input" placeholder="Draft / Voice Input"></textarea>
            <button id="draft-send-button">↵</button>
        `;
        mainLayer.appendChild(draftContainer);

        // --- Ctrl Layer (QWERTY) ---
        const ctrlLayer = document.createElement('div');
        ctrlLayer.id = 'layer-ctrl';
        ctrlLayer.className = 'layer-grid';
        floater.appendChild(ctrlLayer);

        const getCode = (c) => String.fromCharCode(c.charCodeAt(0) - 64);
        const rows = ['QWERTYUIOP', 'ASDFGHJKL', 'ZXCVBNM'];
        rows[0].split('').forEach(c => addKey('^' + c, getCode(c)));
        rows[1].split('').forEach(c => addKey('^' + c, getCode(c)));
        
        addKey(';', ';'); addKey('^B[', '\x02['); addKey('^B]', '\x02]'); addKey('⇧Tab', '\x1b[Z'); addKey('^_', '\x1f'); addKey('Esc', '\x1b');
        rows[2].split('').forEach(c => addKey('^' + c, getCode(c)));
        addKey('⌫', '\x7f'); addKey('␣', ' '); addKey('⇞', '\x1b[5~'); addKey('⇟', '\x1b[6~'); addKey('⌥↵', '\x1b\r'); 

        const backButton = document.createElement('div');
        backButton.className = 'vb-button span-full';
        backButton.textContent = 'BACK';
        backButton.dataset.layer = 'ctrl';
        backButton.dataset.action = 'hideCtrl';
        ctrlLayer.appendChild(backButton);

        function addKey(c, customSeq) {
            const code = customSeq || (c.length === 1 && /[A-Z]/.test(c) ? getCode(c) : c);
            const button = document.createElement('div');
            button.className = 'vb-button';
            button.textContent = c;
            button.dataset.layer = 'ctrl';
            button.dataset.seq = code;
            ctrlLayer.appendChild(button);
        }

        // --- Input Logic ---
        const draftInput = document.getElementById('draft-input');
        const draftSendButton = document.getElementById('draft-send-button');

        const submitDraft = () => {
            const text = draftInput.value;
            if (text) {
                // Send text first
                sendData(text);
                
                // Wait briefly before sending Enter to prevent line break issues
                setTimeout(() => {
                    sendData('\r');
                }, ENTER_DELAY_MS);

                if (navigator.vibrate) navigator.vibrate(50);
                draftInput.value = '';
                draftInput.blur();
            }
        };

        draftInput.addEventListener('keydown', (e) => {
            // Stop propagation to prevent conflict with panel controls
            e.stopPropagation();
            if (e.key === 'Enter' && !e.shiftKey) {
                e.preventDefault();
                submitDraft();
            }
        });
        
        draftSendButton.addEventListener('click', (e) => {
            e.stopPropagation();
            submitDraft();
        });

        // --- Panel Control Logic ---
        let startX, startY, initialLeft, initialTop, isDragging = false, activeButton = null;

        const toggleLayer = (showCtrl) => {
            mainLayer.style.display = showCtrl ? 'none' : 'grid';
            ctrlLayer.style.display = showCtrl ? 'grid' : 'none';
            if (showCtrl) floater.classList.add('mode-ctrl');
            else floater.classList.remove('mode-ctrl');
            if (navigator.vibrate) navigator.vibrate(20);
        };

        floater.addEventListener('touchstart', (e) => {
            // Important: Allow native behavior for the input container
            if (e.target.closest('#draft-container')) return;

            e.preventDefault(); e.stopPropagation();
            const touch = e.touches[0];
            startX = touch.clientX; startY = touch.clientY;
            const rect = floater.getBoundingClientRect();
            initialLeft = rect.left; initialTop = rect.top;
            isDragging = false;
            const target = e.target.closest('.vb-button');
            if (target) { activeButton = target; activeButton.classList.add('active-touch'); }
        }, { passive: false });

        floater.addEventListener('touchmove', (e) => {
            if (e.target.closest('#draft-container')) return;

            e.preventDefault(); e.stopPropagation();
            const touch = e.touches[0];
            const dx = touch.clientX - startX;
            const dy = touch.clientY - startY;
            if (!isDragging && Math.hypot(dx, dy) > DRAG_THRESHOLD) {
                isDragging = true;
                if (activeButton) { activeButton.classList.remove('active-touch'); activeButton = null; }
            }
            if (isDragging) {
                floater.style.left = `${initialLeft + dx}px`;
                floater.style.top = `${initialTop + dy}px`;
                floater.style.right = 'auto'; 
            }
        }, { passive: false });

        floater.addEventListener('touchend', (e) => {
            if (e.target.closest('#draft-container')) return;

            e.preventDefault(); e.stopPropagation();
            if (!isDragging && activeButton) {
                activeButton.classList.remove('active-touch');
                if (activeButton.dataset.action === 'showCtrl') toggleLayer(true);
                else if (activeButton.dataset.action === 'hideCtrl') toggleLayer(false);
                else if (activeButton.dataset.action === 'toggleScroll') {
                    scrollModeActive = !scrollModeActive;
                    activeButton.classList.toggle('toggle-on', scrollModeActive);
                    if (scrollModeActive) sendData('\x02[');
                    if (navigator.vibrate) navigator.vibrate([30, 50]);
                } else {
                    let k = activeButton.dataset.layer === 'main' ? mainKeys[activeButton.dataset.index] : { seq: activeButton.dataset.seq };
                    if (k) {
                        sendData(k.seq || k);
                        if (navigator.vibrate) navigator.vibrate(10);
                        if (activeButton.dataset.layer === 'ctrl') setTimeout(() => toggleLayer(false), 150);
                    }
                }
            }
            activeButton = null; isDragging = false;
        }, { passive: false });
    }
})();
