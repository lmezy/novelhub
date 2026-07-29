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
import re
import subprocess
from typing import Any

logger = logging.getLogger(__name__)

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
        self._ready: bool = False
        self._session_lock = asyncio.Lock()

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
                loop = asyncio.get_event_loop()
                loop.run_until_complete(cls._instance.stop())
            except RuntimeError:
                pass
        cls._instance = None

    async def start(self) -> bool:
        """Start the Node.js subprocess if not already running."""
        async with self._session_lock:
            if self._ready and self._proc is not None and self._proc.returncode is None:
                return True
            return await self._start_impl()

    async def _start_impl(self) -> bool:
        try:
            # A persistent Node.js process that reads eval commands from stdin
            # and writes results to stdout.
            bootstrap = (
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
            self._proc = await asyncio.create_subprocess_exec(
                "node",
                "--no-warnings",
                "-e",
                bootstrap,
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

    async def stop(self) -> None:
        """Stop the Node.js subprocess."""
        async with self._session_lock:
            await self._kill_proc()
            self._ready = False

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

    async def eval_js(self, js_code: str, input_value: Any = None) -> Any:
        """Evaluate a JavaScript expression/code against an input value.

        Args:
            js_code: JavaScript code to execute. Should use ``result`` as
                     the input variable and return the desired output.
            input_value: The value to pass as ``result`` to the JS code.

        Returns:
            The JS evaluation result, or None if execution failed.
        """
        if not self._ready:
            started = await self.start()
            if not started:
                return None

        async with self._session_lock:
            if not self._ready or self._proc is None or self._proc.returncode is not None:
                # Process died, try restart
                await self._start_impl()
                if not self._ready:
                    return None

            try:
                # Wrap the user code: inject input as 'result' and execute
                input_json = json.dumps(input_value)
                user_code = js_code.strip()
                # Build the full eval snippet
                snippet = (
                    f'(function(){{'
                    f'var result={input_json};'
                    f'{user_code}'
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

    async def eval_bytes(self, js_code: str, raw_bytes: bytes) -> bytes | None:
        """Evaluate JS code that operates on byte arrays.

        The JS code receives an ``inputBytes`` variable (Node.js Buffer)
        and should return either a Buffer/Uint8Array or a string.
        Returns decoded bytes, or None on failure.
        """
        if not self._ready:
            started = await self.start()
            if not started:
                return None

        async with self._session_lock:
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

    async def eval_js_with_context(
        self, js_code: str, context: dict[str, Any]
    ) -> Any:
        """Evaluate JS with a multi-variable context.

        The JS code can reference context keys as global variables.
        """
        if not self._ready:
            started = await self.start()
            if not started:
                return None

        async with self._session_lock:
            if not self._ready or self._proc is None or self._proc.returncode is not None:
                await self._start_impl()
                if not self._ready:
                    return None

            try:
                var_decls = []
                for key, value in context.items():
                    var_decls.append(f"var {key}={json.dumps(value)};")
                var_block = " ".join(var_decls)
                snippet = (
                    f'(function(){{{var_block}(function(){{{js_code}}})();}})()'
                )
                cmd = snippet + "\n__CODEX_EVAL_END__\n"
                self._proc.stdin.write(cmd.encode("utf-8"))
                await self._proc.stdin.drain()

                result = await self._read_result()
                return result
            except Exception as e:
                logger.warning(f"JsRuntime context eval error: {e}")
                return None

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
