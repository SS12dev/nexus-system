import json
import uuid
from collections.abc import AsyncIterator

import pytest
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, AIMessageChunk, BaseMessage
from langchain_core.outputs import ChatGeneration, ChatGenerationChunk, ChatResult
from langgraph.checkpoint.memory import InMemorySaver

from nexus_common.models import Chat, RoleName, UserStatus
from nexus_orchestrator.turn import TurnDeps


class ScriptedModel(BaseChatModel):
    """Plays back a script, one entry per model call: a string is streamed word by word as the
    reply; a dict {"tool": name, "args": {...}} is a tool call. The last string repeats once the
    script runs out. Records what it was sent."""

    script: list = ["Hello there, Shivam."]
    error: Exception | None = None
    seen: list = []
    calls: list = []
    tools: list = []
    # Options each bind_tools call got, e.g. a forced tool_choice.
    binds: list = []

    @property
    def _llm_type(self) -> str:
        return "scripted"

    def bind_tools(self, tools, **kwargs):
        self.tools[:] = [t.name for t in tools]
        self.binds.append(kwargs)
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
    return ScriptedModel(seen=[], calls=[], tools=[], binds=[])


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


# --- Agent systems: each agent plays its own script ------------------------------------


class AgentModel(BaseChatModel):
    """Plays a per-agent script: a string is the reply, a dict {"tool", "args"} (or a list of
    them) is a tool call. Records the messages and tool names it was given."""

    agent: str = ""
    model_name: str = ""
    # (scripts, seen), shared with the test; a tuple so pydantic doesn't copy the dicts.
    box: tuple = ({}, {})

    @property
    def scripts(self) -> dict:
        return self.box[0]

    @property
    def seen(self) -> dict:
        return self.box[1]

    @property
    def _llm_type(self) -> str:
        return "agent-scripted"

    def bind_tools(self, tools, **kwargs):
        self.seen.setdefault(f"{self.agent}:tools", []).append([t.name for t in tools])
        return self

    def bind(self, **kwargs):
        return self

    def _generate(self, *a, **k):
        raise NotImplementedError

    async def _agenerate(self, messages, stop=None, run_manager=None, **kw) -> ChatResult:
        self.seen.setdefault(self.agent, []).append(messages)
        self.seen.setdefault("models", []).append((self.agent, self.model_name))
        queue = self.scripts.setdefault(self.agent, [])
        step = queue.pop(0) if queue else f"{self.agent} done"
        if isinstance(step, Exception):
            raise step
        usage = {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15}
        if isinstance(step, dict | list):
            calls = [step] if isinstance(step, dict) else step
            msg = AIMessage(
                "",
                tool_calls=[
                    {"name": c["tool"], "args": c.get("args", {}), "id": uuid.uuid4().hex}
                    for c in calls
                ],
                usage_metadata=usage,
            )
        else:
            msg = AIMessage(step, usage_metadata=usage)
        return ChatResult(generations=[ChatGeneration(message=msg)])


@pytest.fixture
def scripts() -> dict:
    return {}


@pytest.fixture
def seen() -> dict:
    return {}


@pytest.fixture
def sdeps(settings, sessionmaker, redis, gateway, embedder, enqueued, storage, scripts, seen):
    def factory(*, model, api_key, metadata):
        name = metadata.get("generation_name", "")
        agent = name.removeprefix("agent.") if name.startswith("agent.") else name
        seen.setdefault("keys", []).append(api_key)
        return AgentModel(agent=agent, model_name=model, box=(scripts, seen))

    return TurnDeps(
        settings=settings,
        sessionmaker=sessionmaker,
        redis=redis,
        gateway=gateway,
        model_factory=factory,
        embedder=embedder,
        enqueue=enqueued,
        storage=storage,
        checkpointer=InMemorySaver(),
    )


@pytest.fixture
async def owner(sessionmaker):
    from nexus_api.users import create_user

    async with sessionmaker() as db:
        user = await create_user(
            db,
            email="shivam@example.com",
            role=RoleName.owner,
            status=UserStatus.active,
            name="Shivam",
        )
        chat = Chat(user_id=user.id, title="t")
        db.add(chat)
        await db.commit()
        return user, chat.id
