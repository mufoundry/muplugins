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
        self._pending_output = []
        self._repr_highlighter = ReprHighlighter()
        self._syntax_theme = "monokai"

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
            await self.send_event(RichReplEvent(code=raw))
            await self.send_rich("[green]Exiting REPL.[/green]")
            self.session.parser_stack.pop()
            return

        self._history.append(raw)
        await self._eval(raw)

    async def _eval(self, code_str: str):
        await self.send_event(RichReplEvent(code=code_str))
        self._pending_output = []

        try:
            more = await self._runsource(code_str)
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

        if self._pending_output:
            for line in self._pending_output:
                text = Text(line)
                highlighted = self._repr_highlighter(text)
                await self.send_rich(highlighted.markup)

        self._trigger_prompt()

    async def _runsource(self, source: str) -> bool:
        """Compile and run source code, returning True if more input needed."""
        import ast
        from aioconsole import execute

        try:
            compiled = execute.compile_for_aexec(source, "<repl>", "single")
        except (SyntaxError, ValueError) as e:
            await self.send_rich(f"[red]SyntaxError:[/red] {e}")
            return False

        if compiled is None:
            return True

        for tree in compiled:
            coro = execute.make_coroutine_from_tree(tree, "<repl>", local=self._globals)
            try:
                result, new_locals = await coro
            except Exception as e:
                import traceback
                self._pending_output.append(traceback.format_exc())
                return False

            if isinstance(tree, ast.Interactive):
                if result is not None:
                    self._pending_output.append(repr(result) + "\n")

            self._globals.update(new_locals)

        return False

    def _prompt(self):
        pass

    def _trigger_prompt(self):
        pass