from dotenv import load_dotenv
import asyncio, time

from google.genai import types as gtypes
from livekit import agents
from livekit.agents import AgentSession, Agent, RoomInputOptions, FunctionToolsExecutedEvent
from livekit.plugins import (
    openai,
    noise_cancellation,
)
from livekit.plugins import google
from prompts import AGENT_INSTRUCTION, SESSION_INSTRUCTION
from datetime import datetime
from zoneinfo import ZoneInfo

load_dotenv()

from tools import get_weather, web_search, spotify_control

class Assistant(Agent):
    def __init__(self) -> None:
        super().__init__(
            instructions=AGENT_INSTRUCTION 
            + f"\n# CURRENT TIME:\nThe current date and time is {datetime.now(ZoneInfo('Asia/Singapore')).strftime('%Y-%m-%d %H:%M:%S')} (Singapore Time). Prefer up-to-date sources.\n"
            + "\nWhen the user asks to control Spotify, call the tool named \"spotify_control\" with the fields: action, query, uri, device_name, volume.\n",
            llm=google.beta.realtime.RealtimeModel(
                voice="Charon",
                temperature=0.8,
                tool_behavior=gtypes.Behavior.BLOCKING,  # wait for tool result before speaking
                tool_response_scheduling=gtypes.FunctionResponseScheduling.SILENT,
            ),
            tools=[
                get_weather, 
                web_search,
                spotify_control,
            ],
        )


async def entrypoint(ctx: agents.JobContext):

    session = AgentSession(

        
    )

    await session.start(
        room=ctx.room,
        agent=Assistant(),
        room_input_options=RoomInputOptions(
            # For telephony applications, use `BVCTelephony` instead for best results
            video_enabled=True,
            noise_cancellation=noise_cancellation.BVC(),
        ),
    )

    await session.generate_reply(
        instructions=SESSION_INSTRUCTION
    )

    # @session.on(FunctionToolsExecutedEvent)
    # def on_tools_executed(_: FunctionToolsExecutedEvent):
    #     asyncio.create_task(session.generate_reply())

    gen_lock = asyncio.Lock()
    last_kick = 0.0

    async def _safe_generate_reply():
        if gen_lock.locked():
            return
        async with gen_lock:
            try:
                await session.generate_reply()
            except Exception:
                pass

    @session.on(FunctionToolsExecutedEvent)    # sync handler; schedule async work
    def _on_tools_done(_: FunctionToolsExecutedEvent):
        nonlocal last_kick
        now = time.monotonic()
        if now - last_kick < 1.0:  # debounce bursty tool chains
            return
        last_kick = now
        asyncio.create_task(_safe_generate_reply())


if __name__ == "__main__":
    agents.cli.run_app(agents.WorkerOptions(entrypoint_fnc=entrypoint))