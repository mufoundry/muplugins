import ast
import asyncio
import io
import sys
from typing import Optional

import rich.syntax
from rich.console import Console

from ..sessions import SessionParser


class OutputCapture(io.TextIOBase):
    def __init__(self):
        self._buffer = io.StringIO()

    def write(self, text: str) -> int:
        if text:
            self._buffer.write(text)
        return len(text)

    def flush(self):
        pass

    def retrieve(self) -> str:
        result = self._buffer.getvalue()
        self._buffer = io.StringIO()
        return result

    def isatty(self) -> bool:
        return False

    @property
    def encoding(self) -> str:
        return "utf-8"


class REPLParser(SessionParser):
    _INSTRUCTIONS = """[bold cyan]Python REPL[/bold cyan] - Type Python code to execute.
[yellow]awaits[/yellow] are supported. Use 'exit' or 'quit' to leave.
[dim]Available: session, parser, core[/dim]"""

    def __init__(self, session: "Session", developer: bool = False):
        super().__init__(session)
        self._history: list[str] = []
        self._globals: dict = {
            "parser": self,
            "sys": sys,
            "asyncio": asyncio
        }
        session.repl_globals(self._globals)
        self._running = False

    async def start(self):
        await self.send_rich(self._INSTRUCTIONS)
        self._running = True
        self._trigger_prompt()

    async def stop(self):
        self._running = False

    async def execute_command(self, raw: str):
        if not self._running:
            return

        stripped = raw.strip()
        if stripped in ("exit", "quit", "q"):
            await self.send_rich("[green]Exiting REPL.[/green]")
            self.session.parser_stack.pop()
            return

        self._history.append(raw)
        await self._eval(raw)

    async def _eval(self, code: str):
        stdout_capture = OutputCapture()
        stderr_capture = OutputCapture()

        old_stdout, old_stderr = sys.stdout, sys.stderr
        sys.stdout, sys.stderr = stdout_capture, stderr_capture

        try:
            tree = ast.parse(code)
            has_await = any(isinstance(n, ast.Await) for n in ast.walk(tree))

            if has_await:
                wrapped = f"async def _repl():\n{self._indent(code)}"
                
                exec(wrapped, self._globals)
                result = await self._globals["_repl"]()
            else:
                result = exec(code, self._globals)

            if result is not None:
                await self.send_line(repr(result))

        except SyntaxError as e:
            if "incomplete" in str(e).lower():
                self._trigger_prompt()
                return
            await self.send_rich(f"[red]SyntaxError:[/red] {e}")
        except Exception as e:
            await self.send_rich(f"[red]{type(e).__name__}:[/red] {e}")
        finally:
            sys.stdout = old_stdout
            sys.stderr = old_stderr
        
        cap_out = stdout_capture.retrieve()
        if cap_out:
            await self.send_line(cap_out)
        cap_err = stderr_capture.retrieve()
        if cap_err:
            await self.send_line(cap_err)

        self._trigger_prompt()

    def _prompt(self):
        pass

    def _trigger_prompt(self):
        pass