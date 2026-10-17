"""Import/extension smoke test mirroring main.py: load 'cogs' inside `async with client`."""
import asyncio
import os
import sys

os.environ.setdefault("TOKEN", "dummy")
BASE = os.path.dirname(__file__)
sys.path.insert(0, os.path.join(BASE, "repo", "bot"))
os.chdir(os.path.join(BASE, "repo", "bot"))

from core.zyrox import zyrox  # noqa: E402


async def main():
    bot = zyrox()
    async with bot:
        try:
            await bot.load_extension("cogs")
            print("EXTENSION LOADED OK")
            print(f"slash commands: {len(bot.tree.get_commands())}, prefix: {len(bot.commands)}, cogs: {len(bot.cogs)}")
        except Exception:
            import traceback

            traceback.print_exc()


asyncio.run(main())
