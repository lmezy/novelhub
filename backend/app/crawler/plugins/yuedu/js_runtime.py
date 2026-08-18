"""Node.js-based JavaScript runtime for YueDu book source JS evaluation.

Provides real JS execution for:
- webJs: page-level JS (WebView replacement via Playwright)
- coverDecodeJs: cover image decryption
- imageDecode: in-content image decryption
- preUpdateJs: pre-TOC-update script
- formatJs: TOC entry formatting
- loginCheckJs: login status checking

Uses a persistent Node.js subprocess for low-latency evaluation.
Falls back to pattern-based evaluation when Node.js is unavailable.
"""

import asyncio
import base64
import json
import logging
import os
import re
import subprocess
import threading
import tempfile
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# jsoup / java.* shim loaded into the Node.js bootstrap so exported YueDu
# sources using org.jsoup / java.* APIs work without a real JVM.
_JSOUP_SHIM_PATH = Path(__file__).resolve().parent / "jsoup_shim.js"
try:
    _JSOUP_SHIM = _JSOUP_SHIM_PATH.read_text(encoding="utf-8")
except Exception:
    _JSOUP_SHIM = ""

# JS wrapper template that receives code + data, executes, and returns result
_EVAL_WRAPPER = r"""
function __codex_eval__() {
    var result = __codex_input__;
    // __codex_code__
    return result;
}
var __codex_output__ = __codex_eval__();
console.log("__CODEX_RESULT_START__");
console.log(JSON.stringify(__codex_output__));
console.log("__CODEX_RESULT_END__");
"""

# For byte-level operations, the JS receives base64-encoded bytes
_BYTES_EVAL_WRAPPER = r"""
function __codex_eval__() {
    var inputBytes = Buffer.from(__codex_input_b64__, 'base64');
    // __codex_code__
    if (result instanceof Uint8Array || result instanceof ArrayBuffer) {
        result = Buffer.from(result);
    }
    if (Buffer.isBuffer(result) || result instanceof Uint8Array) {
        return "base64:" + Buffer.from(result).toString('base64');
    }
    return result;
}
var __codex_output__ = __codex_eval__();
console.log("__CODEX_RESULT_START__");
console.log(JSON.stringify(__codex_output__));
console.log("__CODEX_RESULT_END__");
"""


class JsRuntime:
    """Persistent Node.js subprocess for evaluating JavaScript.

    Uses a long-lived Node.js process to avoid startup latency.
    Each eval sends code+data via stdin and reads result from stdout.
    """

    _instance: "JsRuntime | None" = None

    def __init__(self):
        self._proc: subprocess.Popen | None = None
        self._bootstrap_path: Path | None = None
        self._ready: bool = False
        self._session_lock: asyncio.Lock | None = None
        # The runtime owns a dedicated event loop so eval methods can be
        # bridged from any caller context: sync code, or an already-running
        # asyncio loop where ``loop.run_until_complete`` would fail.
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(
            target=self._loop.run_forever,
            name="yuedu-js-runtime",
            daemon=True,
        )
        self._thread.start()

    def _get_lock(self) -> asyncio.Lock:
        if self._session_lock is None:
            self._session_lock = asyncio.Lock()
        return self._session_lock

    def _submit(self, coro: Any) -> Any:
        """Schedule a coroutine on the runtime's dedicated loop."""
        return asyncio.run_coroutine_threadsafe(coro, self._loop)

    def _run_sync(self, coro_factory: Any, timeout: float = 120.0) -> Any:
        """Run a coroutine on the dedicated loop and block for the result."""
        future = self._submit(coro_factory())
        try:
            return future.result(timeout=timeout)
        except asyncio.TimeoutError:
            logger.warning("JsRuntime call timed out")
            return None

    def start_sync(self) -> bool:
        try:
            return bool(self._run_sync(lambda: self._start_impl()))
        except Exception:
            return False

    def stop_sync(self) -> None:
        try:
            self._run_sync(self._stop_impl, timeout=30)
        except Exception:
            pass
    @classmethod
    def get_instance(cls) -> "JsRuntime":
        """Get or create the singleton JsRuntime."""
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    @classmethod
    def reset_instance(cls) -> None:
        """Reset the singleton (useful for testing/restart)."""
        if cls._instance is not None:
            try:
                cls._instance.stop_sync()
            except Exception:
                pass
        cls._instance = None

    async def start(self) -> bool:
        """Async alias of start_sync() for await-based callers."""
        return await asyncio.to_thread(self.start_sync)

    def _subprocess_env(self) -> dict:
        """Build the Node subprocess env, forwarding the configured proxy.

        The jsoup shim (Reload / java.get / java.ajax) performs its own
        synchronous curl requests; pointing them at the same proxy keeps
        behavior consistent with the Python httpx client."""
        env = dict(os.environ)
        try:
            from app.services.proxy_config import get_proxy_config
            cfg = get_proxy_config()
            if cfg.enabled:
                proxy = cfg.https_proxy or cfg.http_proxy
                if proxy:
                    env["DSH_HTTP_PROXY"] = proxy
        except Exception:
            pass
        return env

    async def _start_impl(self) -> bool:
        try:
            # A persistent Node.js process that reads eval commands from stdin
            # and writes results to stdout.
            # The shim plus eval loop is too large for a Windows
            # command line (WinError 206), so persist it to a temp file.
            bootstrap_code = _JSOUP_SHIM + (
                'var _buf="";'
                'process.stdin.on("data",function(c){'
                '_buf+=c.toString();'
                'var i;'
                'while((i=_buf.indexOf("\\n__CODEX_EVAL_END__\\n"))!==-1){'
                'var cmd=_buf.slice(0,i);'
                '_buf=_buf.slice(i+22);'
                'try{'
                'var r=eval(cmd);'
                'process.stdout.write('
                '"__CODEX_RESULT_START__\\n"'
                '+JSON.stringify(r)+"\\n__CODEX_RESULT_END__\\n")'
                '}catch(e){'
                'process.stdout.write('
                '"__CODEX_ERROR__\\n"+e.message+"\\n__CODEX_ERROR_END__\\n")'
                '}}});'
                'console.log("__CODEX_READY__")'
            )
            self._bootstrap_path = Path(tempfile.gettempdir()) / (
                f"novelhub_yuedu_bootstrap_{os.getpid()}.js"
            )
            self._bootstrap_path.write_text(bootstrap_code, encoding="utf-8")
            self._proc = await asyncio.create_subprocess_exec(
                "node",
                "--no-warnings",
                str(self._bootstrap_path),
                env=self._subprocess_env(),
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            # Wait for ready signal
            try:
                line = await asyncio.wait_for(
                    self._proc.stdout.readline(), timeout=10
                )
                if b"__CODEX_READY__" in (line or b""):
                    self._ready = True
                    logger.info("Node.js JsRuntime started successfully")
                    return True
            except asyncio.TimeoutError:
                logger.warning("Node.js JsRuntime startup timed out")
                await self._kill_proc()
                return False
        except FileNotFoundError:
            logger.warning("Node.js not found; JS evaluation will use pattern fallback")
            self._ready = False
            return False
        except Exception as e:
            logger.warning(f"Failed to start Node.js JsRuntime: {e}")
            self._ready = False
            return False
        return False

    async def _stop_impl(self) -> None:
        """Stop the Node.js subprocess (runs on the dedicated loop)."""
        async with self._get_lock():
            await self._kill_proc()
            self._ready = False

    async def stop(self) -> None:
        """Async alias of stop_sync() for await-based callers."""
        await asyncio.to_thread(self.stop_sync)

    async def _kill_proc(self) -> None:
        if self._proc:
            try:
                self._proc.stdin.write(b"__CODEX_EXIT__\n")
                self._proc.stdin.close()
            except Exception:
                pass
            try:
                self._proc.kill()
            except Exception:
                pass
            self._proc = None
        if self._bootstrap_path is not None:
            try:
                self._bootstrap_path.unlink(missing_ok=True)
            except Exception:
                pass
            self._bootstrap_path = None

    async def _eval_js_impl(
        self,
        js_code: str,
        input_value: Any = None,
        context: dict[str, Any] | None = None,
    ) -> Any:
        """Evaluate a JavaScript expression/code against an input value.

        Args:
            js_code: JavaScript code to execute. Should use ``result`` as
                     the input variable and return the desired output.
            input_value: The value to pass as ``result`` to the JS code.

        Returns:
            The JS evaluation result, or None if execution failed.
        """
        async with self._get_lock():
            if not self._ready or self._proc is None or self._proc.returncode is not None:
                # Process died, try restart
                await self._start_impl()
                if not self._ready:
                    return None

            try:
                # Wrap the user code: inject input as 'result' and execute.
                # Legado rules communicate through the ``result`` variable,
                # so append a return unless the script returns explicitly.
                input_json = json.dumps(input_value)
                user_code = js_code.strip()
                context_js = ""
                if context:
                    # Inject every context key as a JS variable so rules can
                    # reference baseUrl / bookUrl / url / key / page / chapter
                    # directly, and seed the shim stores (Get/Put and source.*).
                    for key, value in context.items():
                        if isinstance(value, (dict, list, bool, int, float)) or value is None:
                            encoded = json.dumps(value, ensure_ascii=False)
                        else:
                            encoded = json.dumps(str(value), ensure_ascii=False)
                        context_js += f"var {key}={encoded};"
                    ctx_json = json.dumps(context, ensure_ascii=False)
                    context_js += (
                        "if(globalThis.__nhSetVars){globalThis.__nhSetVars(" + 
                        ctx_json + ");}"
                        "if(globalThis.__nhSetSourceConfig){globalThis.__nhSetSourceConfig(" + 
                        ctx_json + ");}"
                    )
                src_json = json.dumps(user_code, ensure_ascii=False)
                snippet = (
                    f'(function(){{'
                    f'{context_js}'
                    f'var result={input_json};'
                    f'var __codex_src__={src_json};'
                    f'var __codex_out__;'
                    f'try{{__codex_out__=eval(__codex_src__);}}'
                    f'catch(e){{'
                    f'{user_code};\n'
                    f'__codex_out__=result;'
                    f'}}'
                    f'return __codex_out__===undefined?result:__codex_out__;'
                    f'}})()'
                )
                cmd = snippet + "\n__CODEX_EVAL_END__\n"
                self._proc.stdin.write(cmd.encode("utf-8"))
                await self._proc.stdin.drain()

                result = await self._read_result()
                return result
            except (BrokenPipeError, ConnectionResetError, OSError) as e:
                logger.warning(f"Node.js JsRuntime connection lost: {e}")
                self._ready = False
                await self._kill_proc()
                return None
            except Exception as e:
                logger.warning(f"JsRuntime eval error: {e}")
                return None

    def eval_js_sync(
        self,
        js_code: str,
        input_value: Any = None,
        context: dict[str, Any] | None = None,
    ) -> Any:
        """Blocking eval for sync callers (rule engine)."""
        if not self._ready and not self.start_sync():
            return None
        try:
            return self._run_sync(
                lambda: self._eval_js_impl(js_code, input_value, context)
            )
        except Exception:
            return None

    async def eval_js(
        self,
        js_code: str,
        input_value: Any = None,
        context: dict[str, Any] | None = None,
    ) -> Any:
        """Async alias of eval_js_sync() for await-based callers."""
        return await asyncio.to_thread(
            self.eval_js_sync, js_code, input_value, context,
        )

    async def _eval_bytes_impl(self, js_code: str, raw_bytes: bytes) -> bytes | None:
        """Evaluate JS code that operates on byte arrays.

        The JS code receives an ``inputBytes`` variable (Node.js Buffer)
        and should return either a Buffer/Uint8Array or a string.
        Returns decoded bytes, or None on failure.
        """
        async with self._get_lock():
            if not self._ready or self._proc is None or self._proc.returncode is not None:
                await self._start_impl()
                if not self._ready:
                    return None

            try:
                b64_input = base64.b64encode(raw_bytes).decode("ascii")
                user_code = js_code.strip()
                snippet = (
                    "(function(){"
                    "var inputBytes=Buffer.from('" + b64_input + "','base64');"
                    "var result;"
                    + user_code +
                    "if(result){"
                    "if(result instanceof Uint8Array||result instanceof ArrayBuffer)"
                    "{result=Buffer.from(result);}"
                    "if(Buffer.isBuffer(result)||result instanceof Uint8Array)"
                    "{return 'base64:'+Buffer.from(result).toString('base64');}"
                    "}"
                    "return result;"
                    "})()"
                )
                cmd = snippet + "\n__CODEX_EVAL_END__\n"
                self._proc.stdin.write(cmd.encode("utf-8"))
                await self._proc.stdin.drain()

                result = await self._read_result()
                if isinstance(result, str) and result.startswith("base64:"):
                    return base64.b64decode(result[7:])
                return None
            except (BrokenPipeError, ConnectionResetError, OSError) as e:
                logger.warning(f"Node.js JsRuntime bytes eval connection lost: {e}")
                self._ready = False
                await self._kill_proc()
                return None
            except Exception as e:
                logger.warning(f"JsRuntime bytes eval error: {e}")
                return None

    def eval_bytes_sync(self, js_code: str, raw_bytes: bytes) -> bytes | None:
        """Blocking bytes eval for sync callers."""
        if not self._ready and not self.start_sync():
            return None
        try:
            return self._run_sync(
                lambda: self._eval_bytes_impl(js_code, raw_bytes)
            )
        except Exception:
            return None

    async def eval_bytes(self, js_code: str, raw_bytes: bytes) -> bytes | None:
        """Async alias of eval_bytes_sync() for await-based callers."""
        return await asyncio.to_thread(self.eval_bytes_sync, js_code, raw_bytes)

    async def _eval_js_with_context_impl(
        self, js_code: str, context: dict[str, Any]
    ) -> Any:
        """Evaluate JS with a multi-variable context.

        The JS code can reference context keys as global variables.
        """
        async with self._get_lock():
            if not self._ready or self._proc is None or self._proc.returncode is not None:
                await self._start_impl()
                if not self._ready:
                    return None

            try:
                var_decls = []
                for key, value in context.items():
                    var_decls.append(f"var {key}={json.dumps(value)};")
                var_block = " ".join(var_decls)
                ctx_json = json.dumps(context, ensure_ascii=False)
                seed_js = (
                    "if(globalThis.__nhSetVars){globalThis.__nhSetVars(" + 
                    ctx_json + ");}"
                    "if(globalThis.__nhSetSourceConfig){globalThis.__nhSetSourceConfig(" + 
                    ctx_json + ");}"
                )
                result_json = json.dumps(context)
                snippet = (
                    f'(function(){{'
                    f'{var_block}'
                    f'{seed_js}'
                    f'var result={result_json};'
                    f'(function(){{{js_code}}})();'
                    f'return result;'
                    f'}})()'
                )
                cmd = snippet + "\n__CODEX_EVAL_END__\n"
                self._proc.stdin.write(cmd.encode("utf-8"))
                await self._proc.stdin.drain()

                result = await self._read_result()
                return result
            except Exception as e:
                logger.warning(f"JsRuntime context eval error: {e}")
                return None

    def eval_js_with_context_sync(
        self, js_code: str, context: dict[str, Any]
    ) -> Any:
        """Blocking context eval for sync callers."""
        if not self._ready and not self.start_sync():
            return None
        try:
            return self._run_sync(
                lambda: self._eval_js_with_context_impl(js_code, context)
            )
        except Exception:
            return None

    async def eval_js_with_context(
        self, js_code: str, context: dict[str, Any]
    ) -> Any:
        """Async alias of eval_js_with_context_sync()."""
        return await asyncio.to_thread(self.eval_js_with_context_sync, js_code, context)

    async def eval_login_js(
        self, js_code: str, login_info: str, password: str,
        base_url: str = ""
    ) -> str | None:
        """Execute a YueDu loginUrl JS with java.* API stubs.

        Writes a Node.js script to a temp file (avoiding shell escaping
        issues) that provides synchronous java.post/get/ajax stubs using
        curl, executes the login JS, and returns cookie strings.
        """
        import tempfile
        import os as _os

        # Build the Node.js script
        script = self._build_login_script(js_code, login_info, password, base_url)

        # Write to temp file
        tmp_path = None
        try:
            fd, tmp_path = tempfile.mkstemp(suffix=".js", prefix="yuedu_login_")
            _os.write(fd, script.encode("utf-8"))
            _os.close(fd)

            proc = await asyncio.create_subprocess_exec(
                "node",
                "--no-warnings",
                tmp_path,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            try:
                stdout, stderr = await asyncio.wait_for(
                    proc.communicate(), timeout=60
                )
            except asyncio.TimeoutError:
                proc.kill()
                await proc.wait()
                logger.warning("Login JS execution timed out")
                return None

            if proc.returncode != 0:
                err_msg = stderr.decode("utf-8", errors="replace")[:500]
                logger.warning(f"Login JS execution failed: {err_msg}")
                return None

            output = stdout.decode("utf-8", errors="replace")
            m = re.search(
                r"__CODEX_RESULT_START__\n(.*?)\n__CODEX_RESULT_END__",
                output, re.DOTALL
            )
            if m:
                try:
                    data = json.loads(m.group(1))
                    cookie = data.get("cookieString", "")
                    if not cookie and data.get("cookies"):
                        cookie = "; ".join(data["cookies"])
                    if not cookie and data.get("result"):
                        result_val = data["result"]
                        if isinstance(result_val, str) and "=" in result_val:
                            cookie = result_val.strip()
                    return cookie if cookie else None
                except json.JSONDecodeError:
                    pass

            # Fallback: find cookie-like strings in output
            cookie_match = re.search(r'([a-zA-Z_]+=[a-zA-Z0-9_\-]+)', output)
            if cookie_match:
                return cookie_match.group(1)

            return None

        except FileNotFoundError:
            logger.warning("Node.js not available for login JS execution")
            return None
        except Exception as e:
            logger.warning(f"Login JS execution error: {e}")
            return None
        finally:
            if tmp_path and _os.path.exists(tmp_path):
                try:
                    _os.unlink(tmp_path)
                except OSError:
                    pass

    @staticmethod
    def _build_login_script(
        js_code: str, login_info: str, password: str, base_url: str
    ) -> str:
        """Build a Node.js script that replicates Legado's login() behavior.

        Legado wraps loginUrl JS as:
            $loginJs
            if(typeof login=='function'){login.apply(this);}

        The JS runtime provides:
        - java: post, get, ajax, setCookie, getLoginInfo, put, get
        - source: same as java
        - baseUrl: source's base URL
        - cookie: cookie store (setCookie reads from here)
        - cache: simple key-value store (put/get)
        - Url(): returns baseUrl
        """
        safe_base_url = json.dumps(base_url)
        safe_js = json.dumps(js_code)

        return f"""// YueDu login script (faithful to Legado BaseSource.login())
const {{ execSync }} = require('child_process');
const baseUrl = {safe_base_url};

// ------- Cache (java.put / java.get / java.getLoginInfo) -------
let _cache = {{}};
// Store the user's credentials in the cache
_cache["_loginInfo"] = JSON.stringify({{"username": {json.dumps(login_info)}, "password": {json.dumps(password)}}});

// ------- Cookie store -------
let _cookies = [];

// ------- HTTP helper -------
function _httpSync(url, method, body, extraHeaders) {{
    try {{
        let headers = Object.assign({{}}, extraHeaders || {{}});
        let headerArgs = '';
        for (let key in headers) {{
            let val = headers[key].replace(/'/g, "'\\\\''");
            headerArgs += " -H '" + key + ": " + val + "'";
        }}
        let bodyArg = '';
        if (body) {{
            let escapedBody = body.replace(/'/g, "'\\\\''");
            bodyArg = " -d '" + escapedBody + "'";
        }}
        let cmd = 'curl -s -S -L --max-time 30 -X ' + method + headerArgs + bodyArg + ' "' + url + '"';
        let output = execSync(cmd, {{ maxBuffer: 10 * 1024 * 1024, timeout: 35000 }});
        return output.toString('utf-8');
    }} catch(e) {{
        return null;
    }}
}}

// ------- java object (faithful to Legado bindings) -------
var java = {{
    post: function(url, body, headers) {{
        return _httpSync(url, 'POST', typeof body === 'string' ? body : JSON.stringify(body), headers);
    }},
    get: function(url) {{
        return _httpSync(url, 'GET', null, null);
    }},
    ajax: function(options) {{
        return _httpSync(options.url, options.method || 'GET', options.body || null, options.headers);
    }},
    setCookie: function(c) {{
        if (c) _cookies.push(c);
    }},
    getLoginInfo: function() {{
        let raw = _cache["_loginInfo"];
        if (!raw) return null;
        try {{ return JSON.parse(raw); }} catch(e) {{ return raw; }}
    }},
    getLoginInfoMap: function() {{
        return java.getLoginInfo();
    }},
    put: function(key, value) {{
        _cache[key] = String(value);
        return value;
    }},
    get: function(key) {{
        return _cache[key] || "";
    }}
}};

// 'source' is the same as 'java' in Legado
var source = java;

// 'cookie' store (Legado uses CookieStore)
var cookie = {{
    getCookie: function() {{ return _cookies.join('; '); }},
    setCookie: function(c) {{ if (c) _cookies.push(c); }}
}};

// 'cache' object
var cache = {{
    put: function(key, value) {{ _cache[key] = String(value); return value; }},
    get: function(key) {{ return _cache[key] || ""; }}
}};

function Url() {{ return baseUrl; }}

// ------- Execute the login JS (Legado-style) -------
var userJsCode = {safe_js};

// Wrap as Legado does: run user JS, then call login() if defined
eval(userJsCode);
if (typeof login === 'function') {{
    try {{
        login.apply(this);
    }} catch(e) {{
        // login() might not exist or might fail - that's OK
    }}
}}

// ------- Output -------
var output = {{
    cookies: _cookies,
    cookieString: _cookies.join('; '),
    cache: _cache
}};

console.log('__CODEX_RESULT_START__');
console.log(JSON.stringify(output));
console.log('__CODEX_RESULT_END__');
"""

    async def _read_result(self) -> Any:
        """Read and parse the result from the Node.js subprocess stdout."""
        lines: list[str] = []
        in_result = False
        had_error = False

        for _ in range(500):  # safety limit
            line = await asyncio.wait_for(
                self._proc.stdout.readline(), timeout=30
            )
            if not line:
                break
            decoded = line.decode("utf-8", errors="replace").strip()

            if decoded == "__CODEX_ERROR__":
                had_error = True
                continue
            if decoded == "__CODEX_ERROR_END__":
                break
            if had_error:
                logger.warning(f"JsRuntime JS error: {decoded}")
                continue

            if decoded == "__CODEX_RESULT_START__":
                in_result = True
                continue
            if decoded == "__CODEX_RESULT_END__":
                break
            if in_result:
                lines.append(decoded)

        if not lines:
            return None
        raw = "\n".join(lines)
        if raw.strip() == "undefined":
            return None
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return raw


def try_eval_js_pattern(js_code: str, raw: Any) -> Any:
    """Fallback pattern-based JS evaluation for simple operations.

    Handles the most common JS patterns found in YueDu book sources
    without requiring a real JS runtime. Used as fast path / fallback.
    """
    code = js_code.strip()

    # JSON.stringify / parse pass-through
    if "JSON.stringify" in code or "JSON.parse" in code:
        return None if not isinstance(raw, str) else raw

    # result.replace(/pattern/flags, "replacement")
    replace_match = re.search(
        r"result\.replace\(\s*/(.+?)/(\w*)\s*,\s*['\"](.+?)['\"]\s*\)",
        code,
    )
    if replace_match:
        pattern = replace_match.group(1)
        flags = replace_match.group(2)
        replacement = replace_match.group(3)
        if isinstance(raw, str):
            cnt = 0 if "g" in flags else 1
            return re.sub(pattern, replacement, raw, count=cnt)

    # return result.field
    field_match = re.search(r"return\s+(?:result|\\$\.)?(\w+)", code)
    if field_match:
        field = field_match.group(1)
        if isinstance(raw, dict) and field in raw:
            return raw[field]

    # result.field
    field_match = re.search(r"result\.(\w+)", code)
    if field_match:
        field = field_match.group(1)
        if isinstance(raw, dict) and field in raw:
            return raw[field]

    # result.match(/pattern/)[n]
    match_match = re.search(
        r"result\.match\(\s*/(.+?)/(\w*)\s*\)\s*\[(\d+)\]", code
    )
    if match_match:
        pattern = match_match.group(1)
        flags = match_match.group(2)
        idx2 = int(match_match.group(3))
        if isinstance(raw, str):
            m = re.search(pattern, raw)
            if m and idx2 <= len(m.groups()):
                return m.group(idx2)

    # String(result).match(/pattern/) or result.match(/pattern/)
    match_string = re.search(
        r"(?:String\(result\)|result)\.match\(\s*/(.+?)/(\w*)\s*\)",
        code,
    )
    if match_string:
        pattern = match_string.group(1)
        if isinstance(raw, str):
            m = re.search(pattern, raw)
            if m:
                return m.group(0)
            return None

    # result.split("sep")[n]
    split_match = re.search(
        r'result\.split\(\s*["\'](.+?)["\']\s*\)\s*\[(\d+)\]', code
    )
    if split_match:
        sep = split_match.group(1)
        idx2 = int(split_match.group(2))
        if isinstance(raw, str):
            parts = raw.split(sep)
            if idx2 < len(parts):
                return parts[idx2]

    # result.trim()
    if "result.trim()" in code:
        if isinstance(raw, str):
            return raw.strip()

    # String(result) or result.toString()
    if "String(result)" in code or "result.toString()" in code:
        return str(raw) if raw is not None else ""

    return None


def try_eval_format_js(js_code: str, value: str) -> str:
    """Pattern-based formatJs evaluation (TOC entry formatting)."""
    code = js_code.strip()
    replace_match = re.search(
        r"result\.replace\(\s*/(.+?)/(\w*)\s*,\s*['\"](.+?)['\"]\s*\)",
        code,
    )
    if replace_match:
        pattern = replace_match.group(1)
        flags = replace_match.group(2)
        replacement = replace_match.group(3)
        cnt = 0 if "g" in flags else 1
        return re.sub(pattern, replacement, value, count=cnt)
    return value
