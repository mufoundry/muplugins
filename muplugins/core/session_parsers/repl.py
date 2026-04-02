import ast
import asyncio
import io
import sys
from typing import Optional

import rich.syntax
from rich.console import Console

from ..sessions import SessionParser


class OutputCapture(io.TextIOBase):
    def __init__(self, send_line):
        self._buffer = io.StringIO()
        self._send_line = send_line

    def write(self, text: str) -> int:
        if text:
            self._buffer.write(text)
        return len(text)

    def flush(self) -> None:
        result = self._buffer.getvalue()
        if result:
            self._send_line(result)
        self._buffer = io.StringIO()

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
        }
        session.repl_globals(self._globals)
        self._running = False

    async def start(self):
        await self.send_line(self._INSTRUCTIONS)
        self._running = True
        self._trigger_prompt()

    async def stop(self):
        self._running = False

    async def execute_command(self, raw: str):
        if not self._running:
            return

        stripped = raw.strip()
        if stripped in ("exit", "quit", "q"):
            await self.send_line("[green]Exiting REPL.[/green]")
            self.session.parser_stack.pop()
            return

        self._history.append(raw)
        await self._eval(raw)

    def _prompt(self):
        self.session.send_event_nowait(
            self.session.core.events["prompt"]()
        )

    async def _eval(self, code: str):
        stdout_capture = OutputCapture(self.send_line)
        stderr_capture = OutputCapture(self.send_line)

        old_stdout, old_stderr = sys.stdout, sys.stderr
        sys.stdout, sys.stderr = stdout_capture, stderr_capture

        try:
            tree = ast.parse(code)
            has_await = any(isinstance(n, ast.Await) for n in ast.walk(tree))

            if has_await:
                wrapped = f"async def _repl():\n{self._indent(code)}"
                exec_globals = {"asyncio": asyncio, **self._globals}
                exec(wrapped, exec_globals)
                result = await exec_globals["_repl"]()
            else:
                result = exec(code, self._globals)

            if result is not None:
                await self.send_line(repr(result))

        except SyntaxError as e:
            if "incomplete" in str(e).lower():
                self._trigger_prompt()
                return
            await self.send_line(f"[red]SyntaxError:[/red] {e}")
        except Exception as e:
            await self.send_line(f"[red]{type(e).__name__}:[/red] {e}")
        finally:
            sys.stdout = old_stdout
            sys.stderr = old_stderr
            stdout_capture.flush()
            stderr_capture.flush()

        self._trigger_prompt()

    def _prompt(self):
        pass
