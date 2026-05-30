"""
Chrome Debug WebUI 提供商

使用 Chrome 调试端口 (CDP) 9222 与浏览器 Tab (ChatGPT, Claude, Gemini, Poe, DeepSeek) 交互。
"""
import httpx
import json
import time
import asyncio
import websockets
from typing import Dict, Any, Optional, List
from ..base import BaseLLMProvider, LLMConfig, LLMResponse, save_llm_log

class CDPClient:
    def __init__(self, ws_url: str):
        self.ws_url = ws_url
        self.ws = None
        self.msg_id = 1

    async def connect(self):
        self.ws = await websockets.connect(self.ws_url, max_size=None)

    async def close(self):
        if self.ws:
            await self.ws.close()

    async def send_command(self, method: str, params: Optional[dict] = None, timeout: float = 30.0) -> dict:
        cmd_id = self.msg_id
        self.msg_id += 1
        payload = {
            "id": cmd_id,
            "method": method,
            "params": params or {}
        }
        await self.ws.send(json.dumps(payload))
        
        start_time = asyncio.get_event_loop().time()
        while True:
            elapsed = asyncio.get_event_loop().time() - start_time
            remaining = max(0.1, timeout - elapsed)
            try:
                resp_str = await asyncio.wait_for(self.ws.recv(), timeout=remaining)
                resp = json.loads(resp_str)
                if resp.get("id") == cmd_id:
                    if "error" in resp:
                        raise Exception(f"CDP Error: {resp['error']}")
                    return resp.get("result", {})
            except asyncio.TimeoutError:
                raise Exception(f"CDP command {method} timed out after {timeout}s")

    async def evaluate(self, expression: str, timeout: float = 30.0) -> Any:
        res = await self.send_command("Runtime.evaluate", {
            "expression": expression,
            "returnByValue": True,
            "awaitPromise": True
        }, timeout=timeout)
        if "result" in res:
            val = res["result"]
            if res.get("exceptionDetails"):
                desc = val.get("description", "Unknown JS error")
                raise Exception(f"Javascript Exception: {desc}")
            if val.get("type") == "undefined":
                return None
            if "value" in val:
                return val["value"]
            elif "description" in val:
                return val["description"]
        return None

class ChromeDebugProvider(BaseLLMProvider):
    """
    Chrome Debug WebUI 提供商
    
    使用 Chrome 调试端口 (CDP) 9222 与浏览器 Tab (ChatGPT, Claude, Gemini, Poe, DeepSeek) 交互。
    """
    PROVIDER_NAME = "chrome_debug"
    
    # 类级别锁，确保并发请求排队执行（同一时间只能在一个浏览器页面输入）
    _lock = asyncio.Lock()

    def _new_conversation_url(self, current_url: str) -> Optional[str]:
        url = (current_url or "").lower()
        if "gemini.google.com" in url:
            return "https://gemini.google.com/app"
        if "chatgpt.com" in url or "chat.openai.com" in url:
            return "https://chatgpt.com/"
        if "claude.ai" in url:
            return "https://claude.ai/new"
        if "chat.deepseek.com" in url:
            return "https://chat.deepseek.com/"
        if "poe.com" in url:
            return "https://poe.com/"
        return None

    async def _start_new_conversation(self, cdp: CDPClient, current_url: str) -> None:
        new_url = self._new_conversation_url(current_url)
        if not new_url:
            return
        print(f"[ChromeDebug] Starting a fresh conversation: {new_url}")
        await cdp.send_command("Page.navigate", {"url": new_url}, timeout=30.0)
        start_time = time.time()
        while time.time() - start_time < 30.0:
            ready = await cdp.evaluate("document.readyState", timeout=5.0)
            has_input = await cdp.evaluate(
                "Boolean(document.querySelector('#prompt-textarea, textarea[data-id=\"root\"], div[contenteditable=\"true\"], div.ql-editor, rich-textarea div, rich-textarea textarea, textarea, [role=\"textbox\"], [aria-label=\"Prompt\"]'))",
                timeout=5.0,
            )
            if ready in ("interactive", "complete") and has_input:
                return
            await asyncio.sleep(0.5)
        raise Exception("Timed out waiting for the new conversation input to become available")

    def _get_endpoint(self) -> str:
        return ""

    def _get_headers(self) -> Dict[str, str]:
        return {}

    def _build_request_body(
        self,
        system_prompt: str,
        user_content: str,
        temperature: float,
        max_tokens: int,
        response_format: Optional[str]
    ) -> Dict[str, Any]:
        return {}

    def _parse_response(self, response_data: Dict[str, Any]) -> str:
        return ""

    async def chat_completion(
        self,
        system_prompt: str,
        user_content: str,
        temperature: float = 0.7,
        max_tokens: int = 4000,
        response_format: Optional[str] = None,
        task_type: str = None,
        novel_id: str = None,
        chapter_id: str = None,
        character_id: str = None
    ) -> LLMResponse:
        start_time = time.time()
        
        # 将系统提示词与用户内容合并
        full_prompt = f"System Instruction:\n{system_prompt}\n\nUser Message:\n{user_content}"
        
        # 调试 URL 默认 http://127.0.0.1:9222
        debug_url = self.config.api_url or "http://127.0.0.1:9222"
        if not debug_url.startswith("http"):
            debug_url = f"http://{debug_url}"
            
        print(f"[ChromeDebug] Waiting for Chrome automation lock to process request...")
        
        async with self._lock:
            duration = 0.0
            try:
                # 1. 访问 Chrome 调试 HTTP 端点获取 tabs
                print(f"[ChromeDebug] Querying Chrome targets at {debug_url}/json")
                async with httpx.AsyncClient(timeout=10.0) as http_client:
                    resp = await http_client.get(f"{debug_url}/json")
                    if resp.status_code != 200:
                        raise Exception(f"Failed to query Chrome targets: {resp.text}")
                    targets = resp.json()
                
                # 2. 筛选 target 页面
                target_model = (self.config.model or "auto").lower()
                pages = [t for t in targets if t.get("type") == "page"]
                if not pages:
                    raise Exception("No open pages found in Chrome. Please open a tab.")
                
                selected_target = None
                
                def is_match(url_str: str, model_str: str) -> bool:
                    url_lower = url_str.lower()
                    if model_str == "chatgpt" and ("chatgpt.com" in url_lower or "chat.openai.com" in url_lower):
                        return True
                    if model_str == "claude" and "claude.ai" in url_lower:
                        return True
                    if model_str == "gemini" and "gemini.google.com" in url_lower:
                        return True
                    if model_str == "poe" and "poe.com" in url_lower:
                        return True
                    if model_str == "deepseek" and "chat.deepseek.com" in url_lower:
                        return True
                    return False
                
                # 精确匹配 model 选择
                if target_model != "auto":
                    for p in pages:
                        if is_match(p.get("url", ""), target_model):
                            selected_target = p
                            break
                            
                # auto-detect fallback (如果没匹配到，或选择了 auto，寻找任意已知 LLM web 页面)
                if not selected_target:
                    for p in pages:
                        for m in ["chatgpt", "claude", "gemini", "poe", "deepseek"]:
                            if is_match(p.get("url", ""), m):
                                selected_target = p
                                break
                        if selected_target:
                            break
                            
                # 如果没有匹配，默认选择第一个 active tab
                if not selected_target:
                    selected_target = pages[0]
                    
                ws_url = selected_target.get("webSocketDebuggerUrl")
                if not ws_url:
                    raise Exception(f"No webSocketDebuggerUrl found for target page: {selected_target.get('title')}")
                
                print(f"[ChromeDebug] Selected tab: '{selected_target.get('title')}' - URL: {selected_target.get('url')}")
                print(f"[ChromeDebug] Connecting via WebSocket to: {ws_url}")
                
                # 3. 建立 CDP 调试连接
                cdp = CDPClient(ws_url)
                await cdp.connect()
                
                try:
                    await self._start_new_conversation(cdp, selected_target.get("url", ""))

                    def make_js_payload(action: str, prompt_text: str = "") -> str:
                        js_template = """
                        (() => {
                            const url = window.location.href;
                            let config = {
                                name: 'generic',
                                inputSelector: 'textarea, div[contenteditable="true"]',
                                sendButtonSelector: 'button[type="submit"], button:has(svg), button',
                                isGenerating: () => false,
                                getLatestResponse: () => {
                                    const botClasses = ['markdown', 'message', 'bubble', 'chat'];
                                    let bestElement = null;
                                    let maxScore = 0;
                                    document.querySelectorAll('div').forEach(div => {
                                        let score = 0;
                                        botClasses.forEach(cls => {
                                            if (div.className && typeof div.className === 'string' && div.className.includes(cls)) {
                                                score += 1;
                                            }
                                        });
                                        if (score > maxScore) {
                                            maxScore = score;
                                            bestElement = div;
                                        }
                                    });
                                    return bestElement ? bestElement.innerText : null;
                                }
                            };

                            if (url.includes('chatgpt.com') || url.includes('chat.openai.com')) {
                                config = {
                                    name: 'chatgpt',
                                    inputSelector: '#prompt-textarea',
                                    sendButtonSelector: 'button[data-testid="send-button"]',
                                    isGenerating: () => {
                                        return document.querySelector('button[data-testid="stop-button"]') !== null;
                                    },
                                    getLatestResponse: () => {
                                        const messages = document.querySelectorAll('div[data-message-author-role="assistant"]');
                                        if (messages.length === 0) return null;
                                        const lastMsg = messages[messages.length - 1];
                                        const markdownBody = lastMsg.querySelector('.markdown') || lastMsg;
                                        return markdownBody.innerText;
                                    }
                                };
                            } else if (url.includes('claude.ai')) {
                                config = {
                                    name: 'claude',
                                    inputSelector: 'div[contenteditable="true"]',
                                    sendButtonSelector: 'button[aria-label="Send Message"], button[aria-label="Send message"]',
                                    isGenerating: () => {
                                        const stopBtn = document.querySelector('button[aria-label="Stop Response"]') || 
                                                        document.querySelector('button[aria-label="Stop response"]') ||
                                                        document.querySelector('button:has(svg rect)');
                                        return stopBtn !== null;
                                    },
                                    getLatestResponse: () => {
                                        const messages = document.querySelectorAll('div[data-testid="message-container"]');
                                        if (messages.length > 0) {
                                            const lastMsg = messages[messages.length - 1];
                                            const content = lastMsg.querySelector('.font-claude-message') || lastMsg;
                                            return content.innerText;
                                        }
                                        const claudeMsgs = document.querySelectorAll('.font-claude-message');
                                        if (claudeMsgs.length > 0) {
                                            return claudeMsgs[claudeMsgs.length - 1].innerText;
                                        }
                                        return null;
                                    }
                                };
                            } else if (url.includes('gemini.google.com')) {
                                config = {
                                    name: 'gemini',
                                    inputSelector: 'rich-textarea div, rich-textarea textarea, div[contenteditable="true"], textarea, [role="textbox"]',
                                    sendButtonSelector: 'button[aria-label="Send message"], send-button button, button[aria-label*="Send"], button[aria-label*="send"], button[aria-label*="Gửi"]',
                                    isGenerating: () => {
                                        const stopBtn = document.querySelector('button[aria-label="Stop response"]') ||
                                                        document.querySelector('button[aria-label="Stop Response"]');
                                        if (stopBtn) return true;
                                        return document.querySelector('mat-progress-bar') !== null;
                                    },
                                    getLatestResponse: () => {
                                        const messages = document.querySelectorAll('message-content');
                                        if (messages.length === 0) return null;
                                        return messages[messages.length - 1].innerText;
                                    }
                                };
                            } else if (url.includes('chat.deepseek.com')) {
                                config = {
                                    name: 'deepseek',
                                    inputSelector: '#chat-input',
                                    sendButtonSelector: 'div[class*="sendButton"]',
                                    isGenerating: () => {
                                        return document.querySelector('div[class*="stopButton"]') !== null;
                                    },
                                    getLatestResponse: () => {
                                        const messages = document.querySelectorAll('.ds-markdown');
                                        if (messages.length === 0) return null;
                                        return messages[messages.length - 1].innerText;
                                    }
                                };
                            } else if (url.includes('poe.com')) {
                                config = {
                                    name: 'poe',
                                    inputSelector: 'textarea[class*="ChatMessageInput"]',
                                    sendButtonSelector: 'button[class*="SendMessageButton"]',
                                    isGenerating: () => {
                                        return document.querySelector('button[class*="StopMessageButton"]') !== null;
                                    },
                                    getLatestResponse: () => {
                                        const messages = document.querySelectorAll('[class*="Message_botMessageBubble"]');
                                        if (messages.length === 0) return null;
                                        return messages[messages.length - 1].innerText;
                                    }
                                };
                            }

                            const action = %ACTION%;
                            const promptText = %PROMPT%;

                            const inputSelectors = Array.from(new Set([
                                config.inputSelector,
                                '#prompt-textarea',
                                'textarea[data-id="root"]',
                                'div[contenteditable="true"]',
                                'div.ql-editor',
                                'rich-textarea div',
                                'rich-textarea textarea',
                                'textarea',
                                '[role="textbox"]',
                                '[aria-label="Prompt"]'
                            ].filter(Boolean)));

                            const sendSelectors = Array.from(new Set([
                                config.sendButtonSelector,
                                'button[data-testid="send-button"]',
                                'button[aria-label="Send prompt"]',
                                'button[aria-label="Send message"]',
                                'button[aria-label*="Send"]',
                                'button[aria-label*="send"]',
                                'button[aria-label*="Gửi"]',
                                'button[data-test-id="send-button"]',
                                'send-button button'
                            ].filter(Boolean)));

                            const isVisible = (el) => {
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
                                        if (isVisible(el)) {
                                            return el;
                                        }
                                    }
                                }
                                throw new Error("Could not find visible input element. Tried selectors: " + inputSelectors.join(", "));
                            };

                            const clearInput = (inputEl) => {
                                inputEl.focus();
                                if (inputEl.tagName === 'TEXTAREA' || inputEl.tagName === 'INPUT') {
                                    const nativeValueSetter = Object.getOwnPropertyDescriptor(window.HTMLTextAreaElement.prototype, "value")?.set
                                        || Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, "value")?.set;
                                    if (nativeValueSetter) {
                                        nativeValueSetter.call(inputEl, "");
                                    } else {
                                        inputEl.value = "";
                                    }
                                    inputEl.dispatchEvent(new Event('input', { bubbles: true }));
                                    inputEl.dispatchEvent(new Event('change', { bubbles: true }));
                                } else {
                                    const selection = window.getSelection();
                                    const range = document.createRange();
                                    range.selectNodeContents(inputEl);
                                    selection.removeAllRanges();
                                    selection.addRange(range);
                                    document.execCommand('delete', false, null);
                                    inputEl.textContent = "";
                                    inputEl.dispatchEvent(new InputEvent('input', {
                                        bubbles: true,
                                        inputType: 'deleteContentBackward',
                                        data: null
                                    }));
                                }
                            };

                            const clickSend = (inputEl) => {
                                for (const selector of sendSelectors) {
                                    let buttons = [];
                                    try {
                                        buttons = Array.from(document.querySelectorAll(selector));
                                    } catch (e) {
                                        continue;
                                    }
                                    for (const sendBtn of buttons) {
                                        if (isVisible(sendBtn) && !sendBtn.disabled) {
                                            sendBtn.removeAttribute('disabled');
                                            sendBtn.click();
                                            return true;
                                        }
                                    }
                                }
                                const enterEvent = new KeyboardEvent('keydown', {
                                    key: 'Enter',
                                    code: 'Enter',
                                    keyCode: 13,
                                    which: 13,
                                    bubbles: true,
                                    cancelable: true
                                });
                                inputEl.dispatchEvent(enterEvent);
                                return false;
                            };

                            if (action === "get_latest") {
                                return config.getLatestResponse();
                            } else if (action === "is_generating") {
                                return config.isGenerating();
                            } else if (action === "prepare_input") {
                                const inputEl = getInput();
                                clearInput(inputEl);
                                return true;
                            } else if (action === "send_current") {
                                const inputEl = getInput();
                                return new Promise((resolve) => {
                                    setTimeout(() => {
                                        clickSend(inputEl);
                                        resolve(true);
                                    }, 500);
                                });
                            } else if (action === "submit") {
                                const inputEl = getInput();
                                
                                if (inputEl.tagName === 'TEXTAREA' || inputEl.tagName === 'INPUT') {
                                    const nativeValueSetter = Object.getOwnPropertyDescriptor(window.HTMLTextAreaElement.prototype, "value")?.set
                                        || Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, "value")?.set;
                                    if (nativeValueSetter) {
                                        nativeValueSetter.call(inputEl, promptText);
                                    } else {
                                        inputEl.value = promptText;
                                    }
                                    inputEl.dispatchEvent(new Event('input', { bubbles: true }));
                                    inputEl.dispatchEvent(new Event('change', { bubbles: true }));
                                } else {
                                    inputEl.focus();
                                    try {
                                        document.execCommand('selectAll', false, null);
                                        document.execCommand('delete', false, null);
                                        document.execCommand('insertText', false, promptText);
                                    } catch (e) {
                                        inputEl.innerText = promptText;
                                        inputEl.dispatchEvent(new Event('input', { bubbles: true }));
                                    }
                                }
                                
                                setTimeout(() => {
                                    clickSend(inputEl);
                                }, 150);
                                return true;
                            }
                            return null;
                        })()
                        """
                        res_js = js_template.replace("%ACTION%", json.dumps(action))
                        res_js = res_js.replace("%PROMPT%", json.dumps(prompt_text))
                        return res_js

                    # 4. 获取发送前的最后一条消息（用于对比）
                    initial_msg = await cdp.evaluate(make_js_payload("get_latest"))
                    
                    # 5. 输入 prompt 并发送
                    print(f"[ChromeDebug] Injecting prompt into Chrome tab...")
                    try:
                        await cdp.evaluate(make_js_payload("prepare_input"))
                        await cdp.send_command("Input.insertText", {"text": full_prompt}, timeout=90.0)
                        await asyncio.sleep(0.5)
                        await cdp.evaluate(make_js_payload("send_current"))
                    except Exception as inject_error:
                        print(f"[ChromeDebug] CDP insertText failed, falling back to DOM submit: {inject_error}")
                        await cdp.evaluate(make_js_payload("submit", full_prompt), timeout=90.0)
                    
                    # 6. 等待其开始生成 (max 20s)
                    print(f"[ChromeDebug] Waiting for generation to start...")
                    start_generating_time = time.time()
                    new_msg = None
                    while time.time() - start_generating_time < 20.0:
                        new_msg = await cdp.evaluate(make_js_payload("get_latest"))
                        is_gen = await cdp.evaluate(make_js_payload("is_generating"))
                        
                        if is_gen or (new_msg and new_msg != initial_msg):
                            break
                        await asyncio.sleep(0.5)
                    else:
                        print("[ChromeDebug] Warning: Response didn't start generating according to indicators, polling anyway...")

                    # 7. 轮询等待生成结束 (max 300s)
                    print(f"[ChromeDebug] Polling for generation to finish...")
                    last_content = new_msg or ""
                    no_change_count = 0
                    start_poll_time = time.time()
                    
                    while time.time() - start_poll_time < 300.0:
                        await asyncio.sleep(1.0)
                        
                        is_gen = await cdp.evaluate(make_js_payload("is_generating"))
                        current_content = await cdp.evaluate(make_js_payload("get_latest"))
                        
                        if current_content is None:
                            current_content = ""
                            
                        if current_content == last_content:
                            if not is_gen:
                                no_change_count += 1
                                if no_change_count >= 2:
                                    break
                        else:
                            last_content = current_content
                            no_change_count = 0
                            
                        print(f"[ChromeDebug] Generated {len(current_content)} chars...")
                    
                    final_content = last_content
                    duration = time.time() - start_time
                    print(f"[ChromeDebug] Generation complete! Length: {len(final_content)} chars. Duration: {duration:.2f}s")
                    
                    # 写入调用日志
                    save_llm_log(
                        provider=self.PROVIDER_NAME,
                        model=self.config.model,
                        system_prompt=system_prompt,
                        user_prompt=user_content,
                        response=final_content,
                        status="success",
                        task_type=task_type,
                        novel_id=novel_id,
                        chapter_id=chapter_id,
                        character_id=character_id,
                        used_proxy=False,
                        duration=duration
                    )
                    
                    return LLMResponse(
                        success=True,
                        content=final_content,
                        duration=duration
                    )
                    
                finally:
                    await cdp.close()
                    
            except Exception as e:
                import traceback
                error_msg = f"Chrome Debug request failed: {str(e)}"
                print(f"[ChromeDebug] Error: {error_msg}")
                traceback.print_exc()
                
                duration = time.time() - start_time
                save_llm_log(
                    provider=self.PROVIDER_NAME,
                    model=self.config.model,
                    system_prompt=system_prompt,
                    user_prompt=user_content,
                    status="error",
                    error_message=error_msg,
                    task_type=task_type,
                    novel_id=novel_id,
                    chapter_id=chapter_id,
                    character_id=character_id,
                    used_proxy=False,
                    duration=duration
                )
                
                return LLMResponse(
                    success=False,
                    error=error_msg,
                    duration=duration
                )
