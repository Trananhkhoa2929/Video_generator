"""Generate asset reference images through an open browser Web UI.

This is used when ComfyUI/local image models are not available. It drives an
already running Chrome instance through the remote debugging port and saves the
generated image into the same user_story folders used by ComfyUI outputs.
"""
import asyncio
import base64
import json
import os
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

import httpx

from app.core.config import get_settings
from app.services.file_storage import file_storage
from app.services.llm.providers.chrome_debug import CDPClient


class WebUIImageService:
    """Small CDP-based image generator for ChatGPT/Gemini web pages."""

    _lock = asyncio.Lock()

    def _get_debug_url(self) -> str:
        env_url = os.getenv("CHROME_DEBUG_URL")
        if env_url:
            debug_url = env_url
        else:
            settings = get_settings()
            if settings.LLM_PROVIDER == "chrome_debug" and settings.LLM_API_URL:
                debug_url = settings.LLM_API_URL
            else:
                debug_url = "http://127.0.0.1:9222"

        if not debug_url.startswith("http"):
            debug_url = f"http://{debug_url}"
        return debug_url.rstrip("/")

    def _is_supported_page(self, url: str) -> bool:
        url_lower = (url or "").lower()
        return (
            "chatgpt.com" in url_lower
            or "chat.openai.com" in url_lower
            or "gemini.google.com" in url_lower
        )

    def _fresh_url(self, url: str) -> Optional[str]:
        url_lower = (url or "").lower()
        if "chatgpt.com" in url_lower or "chat.openai.com" in url_lower:
            return "https://chatgpt.com/"
        if "gemini.google.com" in url_lower:
            return "https://gemini.google.com/app"
        return None

    async def _select_target(self, debug_url: str) -> Dict[str, Any]:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(f"{debug_url}/json")
            resp.raise_for_status()
            targets = resp.json()

        pages = [target for target in targets if target.get("type") == "page"]
        supported = [page for page in pages if self._is_supported_page(page.get("url", ""))]
        if not supported:
            raise RuntimeError(
                "No ChatGPT/Gemini tab found. Open Chrome with --remote-debugging-port=9222, "
                "then open https://chatgpt.com/ or https://gemini.google.com/app."
            )

        # Prefer ChatGPT for image generation, fall back to Gemini when that is
        # the only supported Web UI tab open.
        for page in supported:
            url = (page.get("url") or "").lower()
            if "chatgpt.com" in url or "chat.openai.com" in url:
                return page
        return supported[0]

    async def _wait_for_input(self, cdp: CDPClient, timeout: float = 45.0) -> None:
        selector_check = """
        (() => {
            const selectors = [
                '#prompt-textarea',
                'textarea[data-id="root"]',
                'div[contenteditable="true"]',
                'div.ql-editor',
                'rich-textarea div',
                'rich-textarea textarea',
                'textarea',
                '[role="textbox"]',
                '[aria-label="Prompt"]'
            ];
            const visible = (el) => {
                if (!el) return false;
                const rect = el.getBoundingClientRect();
                const style = window.getComputedStyle(el);
                return rect.width > 0 && rect.height > 0 &&
                    style.display !== 'none' &&
                    style.visibility !== 'hidden' &&
                    style.opacity !== '0';
            };
            return selectors.some((selector) =>
                Array.from(document.querySelectorAll(selector)).some(visible)
            );
        })()
        """
        start = time.time()
        while time.time() - start < timeout:
            ready = await cdp.evaluate("document.readyState", timeout=5.0)
            has_input = await cdp.evaluate(selector_check, timeout=5.0)
            if ready in ("interactive", "complete") and has_input:
                return
            await asyncio.sleep(0.5)
        raise RuntimeError("Timed out waiting for Web UI prompt input")

    async def _start_fresh_conversation(self, cdp: CDPClient, current_url: str) -> None:
        fresh_url = self._fresh_url(current_url)
        if not fresh_url:
            return
        await cdp.send_command("Page.navigate", {"url": fresh_url}, timeout=30.0)
        await self._wait_for_input(cdp)

    async def _select_extended_thinking(self, cdp: CDPClient) -> None:
        script = """
        (async () => {
            const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
            const visible = (el) => {
                if (!el) return false;
                const rect = el.getBoundingClientRect();
                const style = window.getComputedStyle(el);
                const disabled = el.disabled || el.getAttribute('aria-disabled') === 'true';
                return !disabled &&
                    rect.width > 0 &&
                    rect.height > 0 &&
                    style.display !== 'none' &&
                    style.visibility !== 'hidden' &&
                    style.opacity !== '0';
            };
            const textOf = (el) => [
                el.innerText,
                el.textContent,
                el.getAttribute('aria-label'),
                el.getAttribute('title')
            ].filter(Boolean).join(' ').replace(/\\s+/g, ' ').trim();
            const clickEl = (el) => {
                el.scrollIntoView({ block: 'center', inline: 'center' });
                el.dispatchEvent(new MouseEvent('mousedown', { bubbles: true, cancelable: true, view: window }));
                el.dispatchEvent(new MouseEvent('mouseup', { bubbles: true, cancelable: true, view: window }));
                el.click();
            };
            const clickableSelectors = [
                'button',
                '[role="button"]',
                '[aria-haspopup="menu"]',
                '[aria-haspopup="listbox"]',
                'mat-select',
                'mat-chip',
                'div[tabindex="0"]'
            ];
            const optionSelectors = [
                'button',
                '[role="option"]',
                '[role="menuitem"]',
                'mat-option',
                'mat-chip-option',
                'li',
                'div[tabindex="0"]'
            ];
            const findClickable = (patterns) => {
                const elements = clickableSelectors.flatMap((selector) => Array.from(document.querySelectorAll(selector)));
                return elements.find((el) => visible(el) && patterns.some((pattern) => pattern.test(textOf(el))));
            };
            const findOption = (patterns) => {
                const elements = optionSelectors.flatMap((selector) => Array.from(document.querySelectorAll(selector)));
                return elements.find((el) => visible(el) && patterns.some((pattern) => pattern.test(textOf(el))));
            };

            const alreadyExtended = findClickable([/\\bextended\\b/i, /extended thinking/i]);
            if (alreadyExtended) {
                return { changed: false, reason: 'already_extended_visible' };
            }

            const standardControl = findClickable([
                /\\bstandard\\b/i,
                /thinking.*standard/i,
                /standard.*thinking/i
            ]);
            const thinkingControl = standardControl || findClickable([
                /\\bthinking\\b/i,
                /reasoning/i,
                /model/i
            ]);

            if (!thinkingControl) {
                return { changed: false, reason: 'control_not_found' };
            }

            clickEl(thinkingControl);
            await sleep(700);

            const extendedOption = findOption([
                /\\bextended\\b/i,
                /extended thinking/i,
                /think longer/i,
                /more thinking/i
            ]);
            if (!extendedOption) {
                return { changed: false, reason: 'extended_option_not_found' };
            }

            clickEl(extendedOption);
            await sleep(700);
            return { changed: true, reason: 'selected_extended' };
        })()
        """
        try:
            result = await cdp.evaluate(script, timeout=10.0)
            print(f"[WebUIImage] Thinking mode selection: {result}")
        except Exception as exc:
            print(f"[WebUIImage] Thinking mode selection skipped: {exc}")

    async def _send_prompt(self, cdp: CDPClient, prompt: str) -> None:
        script = """
        (async () => {
            const promptText = %PROMPT%;
            const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
            const inputSelectors = [
                '#prompt-textarea',
                'textarea[data-id="root"]',
                'div[contenteditable="true"]',
                'div.ql-editor',
                'rich-textarea div',
                'rich-textarea textarea',
                'textarea',
                '[role="textbox"]',
                '[aria-label="Prompt"]'
            ];
            const sendSelectors = [
                'button[data-testid="send-button"]',
                'button[aria-label="Send prompt"]',
                'button[aria-label="Send message"]',
                'button[aria-label*="Send"]',
                'button[aria-label*="send"]',
                'button[data-test-id="send-button"]',
                'send-button button',
                'button[type="submit"]'
            ];
            const visible = (el) => {
                if (!el) return false;
                const rect = el.getBoundingClientRect();
                const style = window.getComputedStyle(el);
                const disabled = el.disabled || el.getAttribute('aria-disabled') === 'true';
                return !disabled &&
                    rect.width > 0 &&
                    rect.height > 0 &&
                    style.display !== 'none' &&
                    style.visibility !== 'hidden' &&
                    style.opacity !== '0';
            };
            const getInput = () => {
                for (const selector of inputSelectors) {
                    const elements = Array.from(document.querySelectorAll(selector));
                    for (const el of elements) {
                        if (visible(el)) return el;
                    }
                }
                throw new Error('Could not find visible prompt input');
            };

            const input = getInput();
            input.focus();

            if (input.tagName === 'TEXTAREA' || input.tagName === 'INPUT') {
                // Textarea: native setter handles newlines correctly
                const setter = Object.getOwnPropertyDescriptor(window.HTMLTextAreaElement.prototype, 'value')?.set
                    || Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value')?.set;
                if (setter) setter.call(input, '');
                else input.value = '';
                input.dispatchEvent(new Event('input', { bubbles: true }));
                if (setter) {
                    setter.call(input, promptText);
                } else {
                    input.value = promptText;
                }
                input.dispatchEvent(new Event('input', { bubbles: true }));
                input.dispatchEvent(new Event('change', { bubbles: true }));
            } else {
                // contenteditable (ChatGPT ProseMirror / Quill editor):
                // execCommand('insertText') silently drops everything after the first newline,
                // so we write the full text to the clipboard and paste it instead.
                input.focus();

                // Clear existing content first
                const selection = window.getSelection();
                const range = document.createRange();
                range.selectNodeContents(input);
                selection.removeAllRanges();
                selection.addRange(range);
                document.execCommand('delete', false, null);

                // --- Primary strategy: Clipboard API paste ---
                let pastedViaClipboard = false;
                try {
                    await navigator.clipboard.writeText(promptText);
                    document.execCommand('paste');
                    await sleep(300);
                    // Verify that multiline content actually landed
                    const landed = (input.innerText || input.textContent || '').trim();
                    if (landed.length > 0) {
                        pastedViaClipboard = true;
                    }
                } catch (_clipErr) {
                    // Clipboard API may be blocked in some Chrome profiles — fall through
                }

                if (!pastedViaClipboard) {
                    // --- Secondary input strategy: insert line by line using insertText + insertParagraph ---
                    // Clear again in case partial content was inserted
                    range.selectNodeContents(input);
                    selection.removeAllRanges();
                    selection.addRange(range);
                    document.execCommand('delete', false, null);

                    const lines = promptText.split('\\n');
                    for (let i = 0; i < lines.length; i++) {
                        if (lines[i].length > 0) {
                            document.execCommand('insertText', false, lines[i]);
                        }
                        if (i < lines.length - 1) {
                            // insertParagraph creates a proper block-level break that
                            // ProseMirror/Quill recognise, unlike a bare newline character.
                            document.execCommand('insertParagraph', false, null);
                        }
                    }
                }

                input.dispatchEvent(new InputEvent('input', {
                    bubbles: true,
                    inputType: 'insertText',
                    data: promptText
                }));
            }

            const clickSend = () => {
                for (const selector of sendSelectors) {
                    let buttons = [];
                    try {
                        buttons = Array.from(document.querySelectorAll(selector));
                    } catch (_) {
                        continue;
                    }
                    for (const button of buttons) {
                        if (visible(button)) {
                            button.click();
                            return true;
                        }
                    }
                }
                return false;
            };

            for (let attempt = 0; attempt < 30; attempt += 1) {
                if (clickSend()) {
                    return { sent: true, method: 'button', attempt };
                }
                await sleep(500);
            }

            input.focus();
            input.dispatchEvent(new KeyboardEvent('keydown', {
                bubbles: true,
                cancelable: true,
                key: 'Enter',
                code: 'Enter',
                keyCode: 13,
                which: 13
            }));
            input.dispatchEvent(new KeyboardEvent('keyup', {
                bubbles: true,
                cancelable: true,
                key: 'Enter',
                code: 'Enter',
                keyCode: 13,
                which: 13
            }));
            await sleep(1000);
            if (clickSend()) {
                return { sent: true, method: 'button_after_enter' };
            }
            return { sent: false, method: 'none' };
        })()
        """.replace("%PROMPT%", json.dumps(prompt))
        result = await cdp.evaluate(script, timeout=30.0)
        print(f"[WebUIImage] Send prompt result: {result}")
        if not result or not result.get("sent"):
            await cdp.send_command(
                "Input.dispatchKeyEvent",
                {
                    "type": "keyDown",
                    "key": "Enter",
                    "code": "Enter",
                    "windowsVirtualKeyCode": 13,
                    "nativeVirtualKeyCode": 13,
                },
                timeout=5.0,
            )
            await cdp.send_command(
                "Input.dispatchKeyEvent",
                {
                    "type": "keyUp",
                    "key": "Enter",
                    "code": "Enter",
                    "windowsVirtualKeyCode": 13,
                    "nativeVirtualKeyCode": 13,
                },
                timeout=5.0,
            )
            await asyncio.sleep(1.0)
            if not await self._is_generating(cdp):
                raise RuntimeError("Prompt was inserted, but the Web UI did not send it automatically")

    async def _get_image_sources(self, cdp: CDPClient) -> List[Dict[str, Any]]:
        script = """
        (() => {
            const found = [];
            const seen = new Set();

            const absoluteUrl = (src) => {
                if (!src) return '';
                if (src.startsWith('data:image/') || src.startsWith('blob:')) return src;
                try {
                    return new URL(src, document.baseURI).href;
                } catch (_) {
                    return src;
                }
            };

            const visibleRect = (el) => {
                if (!el || !el.getBoundingClientRect) return null;
                const rect = el.getBoundingClientRect();
                const style = window.getComputedStyle(el);
                if (
                    rect.width <= 0 ||
                    rect.height <= 0 ||
                    style.display === 'none' ||
                    style.visibility === 'hidden' ||
                    style.opacity === '0'
                ) {
                    return null;
                }
                return rect;
            };

            const add = (src, width = 0, height = 0, kind = 'unknown', el = null) => {
                const rect = el ? visibleRect(el) : null;
                const normalizedSrc = absoluteUrl(src || '');
                const meta = el ? [
                    el.getAttribute('alt'),
                    el.getAttribute('aria-label'),
                    el.getAttribute('title'),
                    String(el.className || ''),
                    el.getAttribute('role')
                ].filter(Boolean).join(' ') : '';
                const metaLower = meta.toLowerCase();
                const srcLower = normalizedSrc.toLowerCase();
                if (
                    srcLower.includes('avatar') ||
                    srcLower.includes('profile') ||
                    metaLower.includes('avatar') ||
                    metaLower.includes('profile')
                ) {
                    return;
                }

                const rectWidth = rect ? rect.width : 0;
                const rectHeight = rect ? rect.height : 0;
                const finalWidth = Math.max(width || 0, rectWidth || 0);
                const finalHeight = Math.max(height || 0, rectHeight || 0);
                if (!normalizedSrc && (finalWidth < 256 || finalHeight < 256)) return;

                const id = normalizedSrc || `${kind}:${Math.round(rect?.x || 0)}:${Math.round(rect?.y || 0)}:${Math.round(finalWidth)}:${Math.round(finalHeight)}`;
                if (seen.has(id)) return;
                seen.add(id);

                let score = 0;
                if (kind === 'img') score += 30;
                if (normalizedSrc.startsWith('blob:')) score += 70;
                if (normalizedSrc.startsWith('data:image/')) score += 50;
                if (/ai generated|generated image|generated|image animate loaded/.test(metaLower)) score += 90;
                if (/\\bimage\\b|picture|media|asset/.test(metaLower)) score += 20;
                if (finalWidth >= 256 && finalHeight >= 256) score += 20;

                found.push({
                    id,
                    src: normalizedSrc,
                    width: finalWidth,
                    height: finalHeight,
                    x: rect ? rect.x : 0,
                    y: rect ? rect.y : 0,
                    kind,
                    score
                });
            };

            const collect = (root) => {
                Array.from(root.querySelectorAll('img')).forEach((img) => {
                    add(
                        img.currentSrc || img.src || '',
                        img.naturalWidth || img.width || 0,
                        img.naturalHeight || img.height || 0,
                        'img',
                        img
                    );
                });

                Array.from(root.querySelectorAll('source[srcset]')).forEach((source) => {
                    const srcset = source.getAttribute('srcset') || '';
                    const src = srcset.split(',').map((item) => item.trim().split(/\\s+/)[0]).filter(Boolean).pop();
                    add(src, 0, 0, 'srcset', source.parentElement);
                });

                Array.from(root.querySelectorAll('a[href]')).forEach((link) => {
                    const href = link.href || '';
                    if (/\\.(png|jpe?g|webp|gif)(\\?|$)/i.test(href) || href.startsWith('blob:') || href.startsWith('data:image/')) {
                        add(href, 0, 0, 'link', link);
                    }
                });

                Array.from(root.querySelectorAll('canvas')).forEach((canvas, index) => {
                    if ((canvas.width || 0) >= 256 && (canvas.height || 0) >= 256) {
                        try {
                            add(canvas.toDataURL('image/png'), canvas.width, canvas.height, `canvas:${index}`, canvas);
                        } catch (_) {
                            add('', canvas.width, canvas.height, `canvas:${index}`, canvas);
                        }
                    }
                });

                Array.from(root.querySelectorAll('*')).forEach((el) => {
                    const rect = visibleRect(el);
                    if (!rect) return;

                    const style = window.getComputedStyle(el);
                    const bg = style.backgroundImage || '';
                    const match = bg.match(/url\\(["']?(.*?)["']?\\)/);
                    if (match) {
                        add(match[1], rect.width || 0, rect.height || 0, 'background', el);
                    }

                    const role = el.getAttribute('role') || '';
                    const aria = el.getAttribute('aria-label') || '';
                    const className = String(el.className || '');
                    const looksLikeGeneratedImage =
                        rect.width >= 256 &&
                        rect.height >= 256 &&
                        (
                            role === 'img' ||
                            /image|generated|media|asset|picture/i.test(aria) ||
                            /image|generated|media|asset|picture/i.test(className)
                        );
                    if (looksLikeGeneratedImage) {
                        add('', rect.width, rect.height, 'element', el);
                    }

                    if (el.shadowRoot) {
                        collect(el.shadowRoot);
                    }
                });
            };

            collect(document);

            return found
                .filter((img) =>
                    (
                        img.src ||
                        img.kind === 'element' ||
                        img.kind.startsWith('canvas')
                    ) &&
                    (
                        img.src.startsWith('data:image/') ||
                        img.src.startsWith('blob:') ||
                        img.width >= 256 ||
                        img.height >= 256 ||
                        /\\.(png|jpe?g|webp|gif)(\\?|$)/i.test(img.src)
                    )
                )
                .sort((a, b) =>
                    (a.score - b.score) ||
                    ((a.width * a.height) - (b.width * b.height))
                );
        })()
        """
        images = await cdp.evaluate(script, timeout=10.0)
        return images or []

    async def _is_generating(self, cdp: CDPClient) -> bool:
        # NOTE: Gemini uses mat-progress-spinner (not mat-progress-bar).
        # We scope the spinner check to the chat area only to avoid false
        # positives from the sidenav loading spinner which is always present.
        script = """
        (() => Boolean(
            document.querySelector('button[data-testid="stop-button"]') ||
            document.querySelector('button[aria-label="Stop response"]') ||
            document.querySelector('button[aria-label="Stop Response"]') ||
            document.querySelector('mat-progress-bar') ||
            document.querySelector('.chat-history mat-progress-spinner') ||
            document.querySelector('response-container mat-progress-spinner') ||
            document.querySelector('structured-content-container mat-progress-spinner')
        ))()
        """
        try:
            return bool(await cdp.evaluate(script, timeout=5.0))
        except Exception:
            return False

    async def _wait_for_new_image(
        self,
        cdp: CDPClient,
        before_sources: Set[str],
        timeout: float,
    ) -> Dict[str, Any]:
        deadline = time.time() + timeout
        candidate: Optional[str] = None
        stable_hits = 0

        while time.time() < deadline:
            images = await self._get_image_sources(cdp)
            new_images = [img for img in images if (img.get("id") or img.get("src")) not in before_sources]
            if new_images:
                latest = new_images[-1]
                latest_id = latest.get("id") or latest.get("src")
                if latest_id == candidate:
                    stable_hits += 1
                else:
                    candidate = latest_id
                    stable_hits = 0

                if stable_hits >= 2 and not await self._is_generating(cdp):
                    return latest

            await asyncio.sleep(3.0)

        raise RuntimeError("Timed out waiting for a generated image in the Web UI")

    async def _image_to_data_url(self, cdp: CDPClient, src: str) -> str:
        if src.startswith("data:image/"):
            return src

        script = """
        (async () => {
            const src = %SRC%;
            const img = Array.from(document.images).find((item) =>
                (item.currentSrc || item.src) === src
            );
            const url = img ? (img.currentSrc || img.src) : src;
            const response = await fetch(url, { credentials: 'include' });
            if (!response.ok) {
                throw new Error(`Image fetch failed: ${response.status}`);
            }
            const blob = await response.blob();
            return await new Promise((resolve, reject) => {
                const reader = new FileReader();
                reader.onloadend = () => resolve(reader.result);
                reader.onerror = reject;
                reader.readAsDataURL(blob);
            });
        })()
        """.replace("%SRC%", json.dumps(src))
        data_url = await cdp.evaluate(script, timeout=60.0)
        if not isinstance(data_url, str) or not data_url.startswith("data:image/"):
            raise RuntimeError("Web UI did not return a downloadable image")
        return data_url

    def _save_data_url(
        self,
        data_url: str,
        novel_id: str,
        asset_name: str,
        asset_type: str,
    ) -> str:
        header, encoded = data_url.split(",", 1)
        mime = header.split(";", 1)[0].replace("data:", "")
        ext_map = {
            "image/png": ".png",
            "image/jpeg": ".jpg",
            "image/jpg": ".jpg",
            "image/webp": ".webp",
            "image/gif": ".gif",
        }
        ext = ext_map.get(mime, ".png")

        if asset_type == "character":
            path = file_storage.get_character_image_path(novel_id, asset_name)
        elif asset_type == "scene":
            path = file_storage.get_scene_image_path(novel_id, asset_name)
        elif asset_type == "prop":
            path = file_storage.get_prop_image_path(novel_id, asset_name)
        else:
            raise ValueError(f"Unsupported asset image type: {asset_type}")

        path = Path(path).with_suffix(ext)
        path.write_bytes(base64.b64decode(encoded))
        relative_path = path.relative_to(file_storage.base_dir).as_posix()
        return f"/api/files/{relative_path}"

    def _build_web_prompt(
        self,
        prompt: str,
        asset_type: str,
        asset_name: str,
        asset_context: Optional[Dict[str, Any]] = None,
    ) -> str:
        type_instruction = {
            "character": (
                "Create one full-body character reference image. Neutral pose, "
                "clear readable silhouette, clean simple background, no text."
            ),
            "scene": (
                "Create one reusable background/location reference image. "
                "No characters, no text, clear architecture/layout/lighting."
            ),
            "prop": (
                "Create one isolated prop/object reference image. Clean simple "
                "background, clear shape/material/details, no text."
            ),
        }.get(asset_type, "Create one clean visual reference image. No text.")

        context = asset_context or {}
        context_lines = []
        for key in ("description", "appearance", "setting"):
            value = context.get(key)
            if value:
                context_lines.append(f"{key.title()}: {value}")
        context_block = "\n".join(context_lines) if context_lines else "No extra structured asset context."

        return (
            "You are creating a canonical production reference image for a reusable asset.\n"
            f"{type_instruction}\n"
            "Keep the asset consistent with the canonical details below. Do not invent a different identity, outfit, age, material, location, or style.\n\n"
            f"Asset name: {asset_name}\n"
            f"Asset type: {asset_type}\n"
            "Canonical asset card:\n"
            f"{context_block}\n\n"
            "Final image prompt to follow:\n"
            f"{prompt}"
        )

    async def generate_asset_image(
        self,
        prompt: str,
        asset_type: str,
        asset_name: str,
        novel_id: str,
        asset_context: Optional[Dict[str, Any]] = None,
        timeout: float = 240.0,
    ) -> Dict[str, Any]:
        debug_url = self._get_debug_url()
        web_prompt = self._build_web_prompt(prompt, asset_type, asset_name, asset_context)

        async with self._lock:
            cdp: Optional[CDPClient] = None
            try:
                target = await self._select_target(debug_url)
                ws_url = target.get("webSocketDebuggerUrl")
                if not ws_url:
                    raise RuntimeError(f"Selected browser tab has no WebSocket URL: {target.get('title')}")

                cdp = CDPClient(ws_url)
                await cdp.connect()
                await self._start_fresh_conversation(cdp, target.get("url", ""))
                await self._select_extended_thinking(cdp)

                before = {
                    img.get("id") or img.get("src")
                    for img in await self._get_image_sources(cdp)
                    if img.get("id") or img.get("src")
                }

                await self._send_prompt(cdp, web_prompt)
                image_candidate = await self._wait_for_new_image(cdp, before, timeout)

                # FIX: Convert blob URL → data URL IMMEDIATELY after detection,
                # before the blob gets revoked. Gemini revokes blob URLs quickly,
                # so the original 30-second sleep caused all fetches to fail.
                image_src = image_candidate.get("src") or ""
                if not image_src:
                    raise RuntimeError("Generated image has no exposed source URL")

                print("[WebUIImage] Image detected. Fetching blob URL immediately before it is revoked...")
                data_url = await self._image_to_data_url(cdp, image_src)
                print("[WebUIImage] Blob fetched successfully. Waiting 5s for any higher-res replacement...")

                await asyncio.sleep(5.0)

                # Check if a better (larger/higher-res) version appeared after the
                # short wait — e.g. Gemini sometimes replaces the initial preview
                # with a full-resolution image within a few seconds.
                try:
                    settled_images = await self._get_image_sources(cdp)
                    settled_new = [
                        img for img in settled_images
                        if (img.get("id") or img.get("src")) not in before
                    ]
                    if settled_new:
                        better = settled_new[-1]
                        better_src = better.get("src") or ""
                        if better_src and better_src != image_src:
                            print(f"[WebUIImage] Higher-res candidate found ({better_src[:60]}…). Attempting fetch...")
                            try:
                                data_url = await self._image_to_data_url(cdp, better_src)
                                print("[WebUIImage] Higher-res image fetched successfully.")
                            except Exception as fetch_exc:
                                # The replacement blob may already be revoked too;
                                # fall back to the already-captured data_url.
                                print(f"[WebUIImage] Higher-res fetch failed ({fetch_exc}), keeping original.")
                except Exception as settle_exc:
                    # Non-fatal — we already have a valid data_url from the first fetch.
                    print(f"[WebUIImage] Post-wait image scan failed ({settle_exc}), continuing with original.")

                local_url = self._save_data_url(data_url, novel_id, asset_name, asset_type)

                return {
                    "success": True,
                    "image_url": local_url,
                    "message": "Image generated through Web UI",
                }
            except Exception as exc:
                return {
                    "success": False,
                    "message": f"Web UI image generation failed: {exc}",
                }
            finally:
                if cdp:
                    await cdp.close()