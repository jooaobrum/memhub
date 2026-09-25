"""Terminal chatbot for the Habitantes example: a LangChain agent that reads memhub memory (level C middleware).

    python examples/habitantes/chatbot.py [user_id]       # run from the repo root, after `memhub init` + `memhub ingest`

Use `demo-ana` or `demo-bruno` (the users in the sample data) to see their memories injected on every turn.
Anything the agent proposes with `propose_memory` is a candidate: review it with `memhub queue` / `memhub approve`.
"""
import sys
import uuid
from dataclasses import dataclass
from pathlib import Path

from dotenv import find_dotenv, load_dotenv
from langchain.agents import create_agent
from langgraph.checkpoint.memory import InMemorySaver

from memhub.config import build_chat_model, build_embeddings, load_config
from memhub.middleware import MemoryMiddleware
from memhub.service import MemoryService
from memhub.store import MemoryStore

load_dotenv(find_dotenv(usecwd=True))  # OPENROUTER_API_KEY / OPENAI_API_KEY from a git-ignored .env

CONFIG = Path(__file__).with_name("memhub.yaml")
SYSTEM_PROMPT = (
    "You are the assistant of *Habitantes de Grenoble*, a community of Brazilians living in Grenoble. "
    "Answer in the user's language, briefly and concretely. Use the memory you are given about the user "
    "(profile, preferences, past cases) and never invent facts about them."
)


@dataclass
class Ctx:
    workspace_id: str
    user_id: str


def build_service() -> tuple[MemoryService, "object"]:
    settings = load_config(CONFIG)
    store = MemoryStore(settings.database_url, settings.project_prefix, settings.database_schema)
    service = MemoryService(store=store, settings=settings, registry=settings.build_registry(),
                            embeddings=build_embeddings(settings.embeddings))
    return service, settings


def main() -> None:
    user_id = sys.argv[1] if len(sys.argv) > 1 else "demo-ana"
    service, settings = build_service()
    agent = create_agent(
        model=build_chat_model(settings.llm.judge),
        system_prompt=SYSTEM_PROMPT,
        middleware=[MemoryMiddleware(service, workspace_from=settings.workspace_default, user_from=lambda ctx: ctx.user_id)],
        context_schema=Ctx,
        checkpointer=InMemorySaver(),  # keeps the profile snapshot fixed for the whole thread
    )
    config = {"configurable": {"thread_id": str(uuid.uuid4())}}
    ctx = Ctx(workspace_id=settings.workspace_default, user_id=user_id)
    print(f"Chatting as {user_id!r}. Empty line to quit.")
    while (text := input("you> ").strip()):
        result = agent.invoke({"messages": [("user", text)]}, config, context=ctx)
        print(f"bot> {result['messages'][-1].content}\n")


if __name__ == "__main__":
    main()
