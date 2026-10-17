"""Checks for the CV2 builder reorder controls and the mention Developer Info line."""
import asyncio
import os
import sys

BASE = os.path.dirname(__file__)
os.environ.setdefault("TOKEN", "dummy")
sys.path.insert(0, os.path.join(BASE, "repo", "bot"))
os.chdir(os.path.join(BASE, "repo", "bot"))

import core  # noqa: F401  (breaks the utils<->core circular import, like main.py)
import discord

from core.zyrox import zyrox
from utils.cv2_store import empty_template


class Resp:
    def __init__(self):
        self.content = None
        self.view = None

    async def edit_message(self, content=None, view=None):
        self.content, self.view = content, view


class Interaction:
    def __init__(self):
        self.response = Resp()


class Author:
    id = 1
    mention = "<@1>"
    display_name = "tester"


class Ctx:
    guild = type("G", (), {"id": 2})()
    channel = type("C", (), {"id": 3})()
    author = Author()
    prefix = ">"
    bot = None


def block(kind, tag):
    if kind == "text":
        return {"type": "text", "content": tag}
    return {"type": "separator", "divider": True, "spacing": "small"}


async def test_reorder():
    from cogs.commands.cv2 import BlockActionsView, CV2Builder

    ctx = Ctx()
    data = empty_template()
    data["blocks"] = [block("text", "A"), block("text", "B"), block("text", "C")]
    builder = CV2Builder(ctx, data)
    builder.message = None

    panel = BlockActionsView(builder, 0)
    labels = [c.label for c in panel.children]
    print("panel buttons:", labels)
    assert labels == ["Edit", "Remove", "Move up", "Move down", "Close"], labels
    assert panel.children[2].disabled is True, "move up must be disabled on first block"
    assert panel.children[3].disabled is False

    # move the first block down
    i = Interaction()
    await panel.move_down(i)
    order = [b["content"] for b in builder.data["blocks"]]
    print("after move_down:", order, "| panel:", i.response.content)
    assert order == ["B", "A", "C"], order
    assert panel.index == 1
    assert i.response.content == "Managing block `02` of `03`."
    # the re-rendered panel follows the block and re-enables "Move up"
    assert i.response.view.children[2].disabled is False
    assert i.response.view.children[3].disabled is False

    # move it up again
    i2 = Interaction()
    await panel.move_up(i2)
    order = [b["content"] for b in builder.data["blocks"]]
    print("after move_up:", order)
    assert order == ["A", "B", "C"], order
    assert panel.index == 0

    # bottom block cannot move down
    bottom = BlockActionsView(builder, 2)
    assert bottom.children[3].disabled is True
    i3 = Interaction()
    await bottom.move_down(i3)
    order = [b["content"] for b in builder.data["blocks"]]
    assert order == ["A", "B", "C"], order

    # stale panel (block removed elsewhere) must not raise
    stale = BlockActionsView(builder, 2)
    builder.data["blocks"].pop()
    builder.data["blocks"].pop()
    i4 = Interaction()
    await stale.move_up(i4)
    print("stale panel:", i4.response.content)
    assert i4.response.content == "That block no longer exists.", i4.response.content
    assert i4.response.view is None
    print("REORDER OK")


async def test_mention_developer(monkeypatch_name="amrcooked"):
    import cogs.events.mention as m

    m.OWNER_IDS[:] = [631428835405070347]
    m.OWNER_NAME = monkeypatch_name

    class Guild:
        name = "Test Guild"

    class Msg:
        guild = Guild()
        author = Author()

    class Bot:
        user = None

    view = m.MentionSelectView(Msg(), None, ">")

    captured = {}

    class R:
        async def edit_message(self, view=None):
            captured["view"] = view

    class I:
        user = Author()
        data = {"values": ["Developer Info"]}
        response = R()

    await view.on_select(I())
    text = captured["view"].children[0].children[2].content
    print("developer text:", text)
    assert text == "Developed by [amrcooked](https://discord.com/users/631428835405070347)", text
    print("MENTION OK")


async def main():
    await test_reorder()
    await test_mention_developer()


asyncio.run(main())
