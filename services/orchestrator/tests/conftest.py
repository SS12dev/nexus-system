import json
from collections.abc import AsyncIterator

import pytest
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, AIMessageChunk, BaseMessage
from langchain_core.outputs import ChatGeneration, ChatGenerationChunk, ChatResult

from jarvis_orchestrator.turn import TurnDeps


class ScriptedModel(BaseChatModel):
    """Plays back a script, one entry per model call: a string is streamed word by word as the
    reply; a dict {"tool": name, "args": {...}} is a tool call. The last string repeats once the
    script runs out. Records what it was sent."""

    script: list = ["Hello there, Shivam."]
    error: Exception | None = None
    seen: list = []
    calls: list = []
    tools: list = []

    @property
    def _llm_type(self) -> str:
        return "scripted"

    def bind_tools(self, tools, **kwargs):
        self.tools[:] = [t.name for t in tools]
        return self

    def _next(self):
        i = len(self.seen) - 1
        if i < len(self.script):
            return self.script[i]
        return next(s for s in reversed(self.script) if isinstance(s, str))

    def _generate(self, messages, stop=None, run_manager=None, **kw) -> ChatResult:
        raise NotImplementedError

    async def _agenerate(self, messages, stop=None, run_manager=None, **kw) -> ChatResult:
        self.seen.append(messages)
        self.calls.append(kw)
        if self.error:
            raise self.error
        step = self._next()
        return ChatResult(generations=[ChatGeneration(message=AIMessage(step))])

    async def _astream(
        self, messages: list[BaseMessage], stop=None, run_manager=None, **kw
    ) -> AsyncIterator[ChatGenerationChunk]:
        self.seen.append(messages)
        self.calls.append(kw)
        if self.error:
            raise self.error
        step = self._next()
        if isinstance(step, dict):
            yield ChatGenerationChunk(
                message=AIMessageChunk(
                    content="",
                    tool_call_chunks=[
                        {
                            "name": step["tool"],
                            "args": json.dumps(step.get("args", {})),
                            "id": f"call_{len(self.seen)}",
                            "index": 0,
                        }
                    ],
                    usage_metadata={"input_tokens": 5, "output_tokens": 2, "total_tokens": 7},
                )
            )
            return
        words = step.split(" ")
        for i, word in enumerate(words):
            text = word if i == len(words) - 1 else word + " "
            chunk = ChatGenerationChunk(message=AIMessageChunk(content=text))
            if run_manager:
                await run_manager.on_llm_new_token(text, chunk=chunk)
            yield chunk
        yield ChatGenerationChunk(
            message=AIMessageChunk(
                content="",
                usage_metadata={"input_tokens": 12, "output_tokens": 4, "total_tokens": 16},
            )
        )


class Enqueued:
    def __init__(self):
        self.jobs: list[tuple] = []

    async def __call__(self, name, *args, **kwargs):
        self.jobs.append((name, *args))


@pytest.fixture
def model() -> ScriptedModel:
    return ScriptedModel(seen=[], calls=[], tools=[])


@pytest.fixture
def enqueued() -> Enqueued:
    return Enqueued()


@pytest.fixture
def deps(settings, sessionmaker, redis, gateway, embedder, model, enqueued):
    factory_calls: list[dict] = []

    def factory(**kwargs):
        factory_calls.append(kwargs)
        return model

    d = TurnDeps(
        settings=settings,
        sessionmaker=sessionmaker,
        redis=redis,
        gateway=gateway,
        model_factory=factory,
        embedder=embedder,
        enqueue=enqueued,
    )
    d.factory_calls = factory_calls
    return d
