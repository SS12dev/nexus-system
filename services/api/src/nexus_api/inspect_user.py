"""What Nexus holds for one account, for the owner checking that onboarding, the graph and
routines worked: `./scripts/inspect-user.sh someone@example.com`. Read-only. It prints setup
answers, memories and routines, which are that person's own data, so run it only to help
them, and share the output only with them."""

import asyncio
import sys

from sqlalchemy import func, select

from nexus_common.db import make_engine, make_sessionmaker
from nexus_common.models import (
    Chat,
    Entity,
    Feedback,
    Memory,
    PushSubscription,
    Relation,
    Routine,
    RoutineRun,
    User,
)
from nexus_common.routines import describe
from nexus_common.settings import get_settings


def line(label: str, value: object) -> None:
    print(f"  {label:<18} {value}")


async def inspect(email: str) -> int:
    settings = get_settings()
    engine = make_engine(settings.database_url)
    try:
        async with make_sessionmaker(engine)() as db:
            user = await db.scalar(select(User).where(func.lower(User.email) == email.lower()))
            if user is None:
                print(f"No account for {email}")
                return 1
            print(f"\n== {user.email} ({user.name or 'no name'})")
            line("role / status", f"{user.role} / {user.status}")
            line("assistant", user.assistant)
            line("signed up", f"{user.created_at:%Y-%m-%d %H:%M}, last in {user.last_login_at}")
            line("onboarded", user.onboarded_at)
            line("terms", f"{user.terms_version} at {user.terms_accepted_at}")
            line("time zone", user.timezone)
            line("phone", "set" if user.phone else "none")
            line("share usage", user.share_usage)
            for k, v in (user.profile or {}).items():
                line(f"profile.{k}", v)

            chats = await db.scalar(select(func.count()).where(Chat.user_id == user.id))
            pushes = await db.scalar(
                select(func.count()).where(PushSubscription.user_id == user.id)
            )
            feedback = await db.scalar(select(func.count()).where(Feedback.user_id == user.id))
            line("chats", chats)
            line("push devices", pushes)
            line("feedback sent", feedback)

            memories = (
                await db.scalars(
                    select(Memory).where(Memory.user_id == user.id).order_by(Memory.created_at)
                )
            ).all()
            print(f"\n-- memories ({len(memories)})")
            for m in memories:
                linked = "linked" if m.graph_at else "waiting for graph job"
                pin = "pinned " if m.pinned else ""
                print(f"  [{m.kind:<11}] {pin}{m.source:<10} {linked:<22} {m.content[:90]}")

            entities = (await db.scalars(select(Entity).where(Entity.user_id == user.id))).all()
            names = {e.id: ("YOU" if e.is_self else e.name) for e in entities}
            relations = (
                await db.scalars(select(Relation).where(Relation.user_id == user.id))
            ).all()
            print(f"\n-- knowledge graph ({len(entities)} entities, {len(relations)} relations)")
            for e in sorted(entities, key=lambda e: (not e.is_self, e.type, e.name)):
                print(f"  {e.type:<12} {names[e.id]}")
            for r in relations:
                ended = f" (ended {r.valid_to:%Y-%m-%d})" if r.valid_to else ""
                print(f"  {names.get(r.src_id)} --{r.predicate}--> {names.get(r.dst_id)}{ended}")

            routines = (
                await db.scalars(
                    select(Routine).where(Routine.user_id == user.id).order_by(Routine.created_at)
                )
            ).all()
            print(f"\n-- routines ({len(routines)})")
            for r in routines:
                state = "on " if r.enabled else "off"
                print(
                    f"  {state} {r.name:<30} {describe(r.schedule):<44} next {r.next_run_at} "
                    f"({r.timezone}) via {','.join(r.delivery) or 'chat'} fails {r.failures}"
                )
                runs = (
                    await db.scalars(
                        select(RoutineRun)
                        .where(RoutineRun.routine_id == r.id)
                        .order_by(RoutineRun.started_at.desc())
                        .limit(3)
                    )
                ).all()
                for run in runs:
                    tokens = f"{run.input_tokens or 0}+{run.output_tokens or 0} tokens"
                    err = f" error: {run.error[:80]}" if run.error else ""
                    print(f"      {run.started_at:%m-%d %H:%M} {run.status:<8} {tokens}{err}")
    finally:
        await engine.dispose()
    return 0


def main() -> None:
    if len(sys.argv) != 2:
        print("usage: python -m nexus_api.inspect_user someone@example.com")
        raise SystemExit(2)
    raise SystemExit(asyncio.run(inspect(sys.argv[1])))


if __name__ == "__main__":
    main()
