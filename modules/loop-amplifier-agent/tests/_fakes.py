"""Handle doubles only; all records come from the real public binding."""

import asyncio
from types import SimpleNamespace

from amplifier_agent import (
    Event,
    Selection,
    SessionRecord,
    TextPart,
    TurnResult,
    TurnStarted,
)


class CapturingHooks:
    def __init__(self):
        self.events = []

    async def emit(self, name, data):
        self.events.append((name, data))

    @property
    def completion(self):
        return [data for name, data in self.events if name == "orchestrator:complete"][
            -1
        ]


class FakeContextManager:
    def __init__(self, messages=None):
        self.messages = messages or []

    async def get_messages(self):
        return self.messages


def coordinator():
    return SimpleNamespace(
        config={
            "providers": [
                {
                    "module": "provider-anthropic",
                    "config": {"default_model": "claude-sonnet-5-5"},
                }
            ],
            "agents": {"reviewer": {}},
        },
        get_capability=lambda _: None,
    )


class Handles:
    def __init__(self, result=None, payloads=None, wait=False):
        self.result = result or TurnResult("success", [TextPart("ok")])
        self.payloads, self.wait = payloads, wait
        self.ready, self.cancelled = asyncio.Event(), asyncio.Event()
        self.consumers = 0
        self.closes, self.close_errors = [], {}
        self.start_error = self.session_error = self.stream_error = None
        self.session_options = self.options = self.input = None
        self.session = SimpleNamespace(
            info=SessionRecord(
                "actual-session", "ephemeral", "actual-provider", "actual-model"
            ),
            start_turn=self.start_turn,
            close=lambda: self.close("session"),
        )
        self.agent = SimpleNamespace(
            create_session=self.create_session, close=lambda: self.close("agent")
        )
        self.turn = SimpleNamespace(events=self.events, cancel=self.cancel)

    async def create_agent(self, options):
        self.options = options
        return self.agent

    async def create_session(self, options):
        self.session_options = options
        if self.session_error:
            raise self.session_error
        return self.session

    async def start_turn(self, input):
        self.input = input
        if self.start_error:
            raise self.start_error
        return self.turn

    async def close(self, name):
        self.closes.append(name)
        if name in self.close_errors:
            raise self.close_errors[name]

    async def cancel(self):
        self.cancelled.set()

    async def events(self):
        self.consumers += 1
        assert self.consumers == 1
        yield Event(
            "turn-events/1",
            "actual-session",
            "actual-turn",
            1,
            "turn_started",
            TurnStarted("fresh", Selection("actual-provider", "actual-model"), "low"),
        )
        self.ready.set()
        if self.wait:
            await self.cancelled.wait()
        if self.stream_error:
            raise self.stream_error
        payloads = (
            self.payloads if self.payloads is not None else [("terminal", self.result)]
        )
        for sequence, (kind, payload) in enumerate(payloads, 2):
            yield Event(
                "turn-events/1",
                "actual-session",
                "actual-turn",
                sequence,
                kind,
                payload,
            )
