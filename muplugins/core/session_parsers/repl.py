import asyncio
import code
import codeop
import io
import sys
from typing import Optional

from ..events.messages import RichReplEvent
from rich.text import Text
from rich.highlighter import ReprHighlighter
import rich.syntax
from ..sessions import SessionParser
from aioconsole import AsynchronousConsole
from aioconsole import execute


class REPLParser(SessionParser):
    _INSTRUCTIONS = """[bold cyan]Python REPL[/bold cyan] - Type Python code to execute.
[yellow]awaits[/yellow] are supported. Use 'exit' or 'quit' to leave."""

    def __init__(self, session: "Session", developer: bool = False):
        super().__init__(session)
        self._history: list[str] = []
        self._globals: dict = {
            "parser": self
        }
        session.repl_globals(self._globals)
        self._running = False
        self._repr_highlighter = ReprHighlighter()
        self._syntax_theme = "monokai"

        class OutCapture(io.TextIOBase):
            def __init__(self, callback):
                self._callback = callback
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

        class REPLConsole(AsynchronousConsole):
            def __init__(self, locals, out_capture):
                super().__init__(locals=locals)
                self._out_capture = out_capture

            async def runcode(self, code_obj):
                try:
                    await execute.aexec(
                        code_obj, local=self.locals, stream=self, filename=self.filename
                    )
                except SystemExit:
                    raise
                except BaseException:
                    self.showtraceback()
                    await self.flush()

            def write(self, data):
                if isinstance(data, str):
                    self._out_capture.write(data)
                else:
                    self._out_capture.write(data.decode())

            async def flush(self):
                pass

        self._out_capture = OutCapture(self._output_received)
        self.console = REPLConsole(self._globals, self._out_capture)

    def _output_received(self, output: str):
        pass

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
            await self.send_event(RichReplEvent(code=raw, prompt=">>>"))
            await self.send_rich("[green]Exiting REPL.[/green]")
            self.session.parser_stack.pop()
            return

        self._history.append(raw)
        await self._eval(raw)

    async def _eval(self, code_str: str):
        await self.send_event(RichReplEvent(code=code_str, prompt=">>>"))

        try:
            more = await self.console.push(code_str)
            if more:
                await self.send_rich("[yellow]Incomplete input[/yellow]")
        except SyntaxError as e:
            await self.send_rich(f"[red]SyntaxError:[/red] {e}")
        except Exception as e:
            import traceback
            await self.send_rich(f"[red]{type(e).__name__}:[/red] {e}")
            traceback_text = Text(traceback.format_exc())
            highlighted = self._repr_highlighter(traceback_text)
            await self.send_rich(highlighted.markup)

        output = self._out_capture.retrieve()
        if output:
            for line in output.rstrip("\n").split("\n"):
                if line:
                    text = Text(line)
                    highlighted = self._repr_highlighter(text)
                    await self.send_rich(highlighted.markup)

        self._trigger_prompt()

    def _prompt(self):
        pass

    def _trigger_prompt(self):
        pass