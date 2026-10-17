"""Components V2 layouts: interactive builder, saved templates and reuse.

Commands
--------
``cv2``                  open the builder (same feel as ``embed``)
``cv2 build [name]``     create a new layout, or edit a saved one
``cv2 send <name>``      send a saved layout to a channel
``cv2 preview <name>``   privately preview a saved layout
``cv2 list``             list saved layouts
``cv2 edit <name>``      open a saved layout in the builder
``cv2 delete <name>``    delete a saved layout
``cv2 export <name>``    download the layout as JSON
``cv2 import``           upload a layout from JSON

Saved layouts are addressed by name and become available to other features
(welcome messages use ``greet`` -> CV2, and any cog can call
``utils.cv2_store.send_template``).
"""

from __future__ import annotations

import io
import json
import re
from typing import Any, Optional

import discord
from discord import app_commands, ui
from discord.ext import commands

from utils.cv2_actions import execute_button
from utils.cv2_store import (
    MAX_BLOCKS,
    ACTION_TYPES,
    CV2Error,
    build_view,
    delete_template,
    describe_block,
    empty_template,
    get_template,
    get_template_by_id,
    init_db,
    list_templates,
    new_button_id,
    normalize_block,
    normalize_button,
    normalize_template,
    save_template,
    template_names,
)
from utils.emoji import CROSS, TICK
from utils.Tools import blacklist_check, ignore_check
from utils.varhelp import VariablesView, variables_embed
from utils.variables import build_variables

RED = 0xFF0000

ACTION_LABELS = {
    "link": "Link button (opens a URL)",
    "role_add": "Add role",
    "role_remove": "Remove role",
    "role_toggle": "Toggle role",
    "ephemeral": "Private reply",
    "dm": "Send a direct message",
    "channel_message": "Post in a channel",
}

ACTION_STYLES = {
    "link": "link",
    "role_add": "primary",
    "role_remove": "danger",
    "role_toggle": "primary",
    "ephemeral": "secondary",
    "dm": "secondary",
    "channel_message": "success",
}


def _short(text: str, limit: int = 90) -> str:
    text = " ".join(str(text).split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _is_emoji_field(value: str) -> bool:
    """True when a menu line's first field is an emoji rather than a label."""
    value = value.strip()
    if not value:
        return False
    if value.startswith("<") and value.endswith(">"):  # <:name:id> / <a:name:id>
        return True
    return not any(char.isalnum() for char in value)


def _parse_menu_options(raw: str) -> list[dict[str, Any]]:
    """Parse the builder's one-option-per-line menu syntax.

    Accepted forms::

        Label
        Label | Description
        Emoji | Label
        Emoji | Label | Description
    """
    options: list[dict[str, Any]] = []
    for line in (raw or "").splitlines():
        line = line.strip()
        if not line:
            continue

        parts = [part.strip() for part in line.split("|")]
        emoji = None
        description = ""
        if len(parts) >= 2 and _is_emoji_field(parts[0]):
            emoji, label = parts[0], parts[1]
            description = " | ".join(parts[2:]).strip()
        elif len(parts) >= 3 and not parts[0]:
            label = parts[1]
            description = " | ".join(parts[2:]).strip()
        elif len(parts) >= 2:
            label = parts[0]
            description = " | ".join(parts[1:]).strip()
        else:
            label = line

        options.append(
            {
                "label": label,
                "description": description or None,
                "emoji": emoji,
            }
        )
    return options


def _menu_options_text(options: list[dict[str, Any]]) -> str | None:
    """Render stored options back into the modal's line format."""
    if not options:
        return None
    lines: list[str] = []
    for option in options:
        bits = [option["label"]]
        if option.get("description"):
            bits.append(option["description"])
        if option.get("emoji"):
            bits.insert(0, option["emoji"])
        lines.append(" | ".join(bits))
    return "\n".join(lines)


def _parse_role_ids(raw: str) -> list[int]:
    ids: list[int] = []
    for token in re.split(r"[,\s]+", raw or ""):
        match = re.search(r"\d{15,25}", token)
        if match:
            role_id = int(match.group())
            if role_id not in ids:
                ids.append(role_id)
    return ids


async def template_autocomplete(
    interaction: discord.Interaction, current: str
) -> list[app_commands.Choice[str]]:
    if interaction.guild_id is None:
        return []
    names = await template_names(interaction.guild_id)
    return [
        app_commands.Choice(name=name, value=name)
        for name in names
        if current.lower() in name.lower()
    ][:25]


# ── Modals ────────────────────────────────────────────────────────────────────


class _BuilderModal(ui.Modal):
    def __init__(self, builder: "CV2Builder", *, title: str):
        super().__init__(title=title, timeout=600)
        self.builder = builder

    async def _author_ok(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.builder.ctx.author.id:
            await interaction.response.send_message(
                "This builder belongs to someone else.", ephemeral=True
            )
            return False
        return True

    async def _finish(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer()
        await self.builder.refresh()


class TextBlockModal(_BuilderModal):
    def __init__(self, builder: "CV2Builder", index: int | None = None):
        super().__init__(builder, title="Text block" if index is None else "Edit text block")
        self.index = index
        default = builder.data["blocks"][index]["content"] if index is not None else None
        self.text = ui.TextInput(
            style=discord.TextStyle.paragraph,
            default=default,
            max_length=4000,
            required=True,
        )
        self.add_item(ui.Label(text="Text (markdown allowed)", component=self.text))

    async def on_submit(self, interaction: discord.Interaction):
        if not await self._author_ok(interaction):
            return
        try:
            block = normalize_block({"type": "text", "content": self.text.value})
        except CV2Error as exc:
            await interaction.response.send_message(str(exc), ephemeral=True)
            return
        if self.index is None:
            self.builder.data["blocks"].append(block)
        else:
            self.builder.data["blocks"][self.index] = block
        await self._finish(interaction)


class SectionBlockModal(_BuilderModal):
    def __init__(self, builder: "CV2Builder", index: int | None = None):
        super().__init__(builder, title="Section" if index is None else "Edit section")
        self.index = index
        block = builder.data["blocks"][index] if index is not None else {}
        self.text = ui.TextInput(
            style=discord.TextStyle.paragraph,
            default=block.get("text", [""])[0] if block else None,
            max_length=4000,
            required=True,
        )
        self.thumbnail = ui.TextInput(
            default=block.get("thumbnail") if block else None,
            max_length=512,
            required=False,
        )
        self.add_item(ui.Label(text="Section text", component=self.text))
        self.add_item(
            ui.Label(
                text="Thumbnail URL (optional)",
                description="An http(s) image URL. {user_avatar} and {server_icon} work in welcome messages.",
                component=self.thumbnail,
            )
        )

    async def on_submit(self, interaction: discord.Interaction):
        if not await self._author_ok(interaction):
            return
        try:
            block = normalize_block(
                {
                    "type": "section",
                    "text": [self.text.value],
                    "thumbnail": self.thumbnail.value.strip() or None,
                }
            )
        except CV2Error as exc:
            await interaction.response.send_message(str(exc), ephemeral=True)
            return
        if self.index is None:
            self.builder.data["blocks"].append(block)
        else:
            self.builder.data["blocks"][self.index] = block
        await self._finish(interaction)


class GalleryBlockModal(_BuilderModal):
    def __init__(self, builder: "CV2Builder", index: int | None = None):
        super().__init__(builder, title="Image gallery" if index is None else "Edit gallery")
        self.index = index
        block = builder.data["blocks"][index] if index is not None else {}
        default = "\n".join(block.get("items", [])) if block else None
        self.items = ui.TextInput(
            style=discord.TextStyle.paragraph,
            default=default,
            max_length=4000,
            required=True,
        )
        self.add_item(
            ui.Label(
                text="Image URLs (one per line, max 10)",
                component=self.items,
            )
        )

    async def on_submit(self, interaction: discord.Interaction):
        if not await self._author_ok(interaction):
            return
        try:
            block = normalize_block(
                {"type": "gallery", "items": self.items.value.splitlines()}
            )
        except CV2Error as exc:
            await interaction.response.send_message(str(exc), ephemeral=True)
            return
        if self.index is None:
            self.builder.data["blocks"].append(block)
        else:
            self.builder.data["blocks"][self.index] = block
        await self._finish(interaction)


class MenuBlockModal(_BuilderModal):
    def __init__(self, builder: "CV2Builder", index: int | None = None):
        super().__init__(builder, title="Menu" if index is None else "Edit menu")
        self.index = index
        block = builder.data["blocks"][index] if index is not None else {}
        multi = int(block.get("max_values", 1) or 1) > 1

        self.placeholder_input = ui.TextInput(
            default=block.get("placeholder"),
            placeholder="Pick a role…",
            max_length=150,
            required=False,
        )
        self.options_input = ui.TextInput(
            style=discord.TextStyle.paragraph,
            default=_menu_options_text(block.get("options", [])),
            placeholder="🎉 | Giveaway | Get pinged for events\n🛠 | Support | Open a ticket",
            max_length=4000,
            required=True,
        )
        self.mode = ui.Select(
            placeholder="How many options can be picked?",
            options=[
                discord.SelectOption(
                    label="One option",
                    value="one",
                    description="Standard dropdown",
                    default=not multi,
                ),
                discord.SelectOption(
                    label="Several options",
                    value="many",
                    description="Pick more than one at a time",
                    default=multi,
                ),
            ],
        )
        self.add_item(
            ui.Label(text="Placeholder (optional)", component=self.placeholder_input)
        )
        self.add_item(
            ui.Label(
                text="Options — one per line",
                description="emoji | label | description · the emoji and description are optional",
                component=self.options_input,
            )
        )
        self.add_item(ui.Label(text="Selection mode", component=self.mode))

    async def on_submit(self, interaction: discord.Interaction):
        if not await self._author_ok(interaction):
            return

        options = _parse_menu_options(self.options_input.value)
        if not options:
            await interaction.response.send_message(
                "Add at least one option.", ephemeral=True
            )
            return

        multi = bool(self.mode.values) and self.mode.values[0] == "many"
        existing = (
            self.builder.data["blocks"][self.index] if self.index is not None else {}
        )
        try:
            block = normalize_block(
                {
                    "type": "menu",
                    "id": existing.get("id"),
                    "placeholder": self.placeholder_input.value.strip() or None,
                    "min_values": 1,
                    "max_values": len(options) if multi else 1,
                    "disabled": existing.get("disabled", False),
                    "options": options,
                }
            )
        except CV2Error as exc:
            await interaction.response.send_message(str(exc), ephemeral=True)
            return

        if self.index is None:
            self.builder.data["blocks"].append(block)
        else:
            self.builder.data["blocks"][self.index] = block
        await self._finish(interaction)


class AccentModal(_BuilderModal):
    def __init__(self, builder: "CV2Builder"):
        super().__init__(builder, title="Container accent color")
        current = builder.data.get("accent_color")
        self.color = ui.TextInput(
            default=f"#{current:06X}" if isinstance(current, int) else None,
            placeholder="#FF0000 (leave empty for Discord's default)",
            max_length=7,
            required=False,
        )
        self.add_item(ui.Label(text="Hex color", component=self.color))

    async def on_submit(self, interaction: discord.Interaction):
        if not await self._author_ok(interaction):
            return
        try:
            clean = normalize_template(
                {**self.builder.data, "accent_color": self.color.value.strip() or None}
            )
        except CV2Error as exc:
            await interaction.response.send_message(str(exc), ephemeral=True)
            return
        self.builder.data["accent_color"] = clean["accent_color"]
        await self._finish(interaction)


class ButtonModal(_BuilderModal):
    def __init__(self, builder: "CV2Builder"):
        super().__init__(builder, title="Add a button")
        self.action = ui.Select(
            placeholder="What should the button do?",
            options=[
                discord.SelectOption(
                    label=ACTION_LABELS[key],
                    value=key,
                    description="Opens a URL — no bot action runs"
                    if key == "link"
                    else None,
                )
                for key in ACTION_TYPES
            ],
        )
        self.label = ui.TextInput(max_length=80, required=True)
        self.roles = ui.TextInput(
            placeholder="123456789012345678, 234567890123456789",
            required=False,
            max_length=400,
        )
        self.message = ui.TextInput(
            style=discord.TextStyle.paragraph,
            placeholder="Supports {user}, {server_name}, {user_name} …",
            required=False,
            max_length=2000,
        )
        self.url = ui.TextInput(
            placeholder="https://example.com",
            required=False,
            max_length=512,
        )
        self.add_item(ui.Label(text="Action", component=self.action))
        self.add_item(ui.Label(text="Label", component=self.label))
        self.add_item(
            ui.Label(
                text="Roles (role actions only)",
                description="Role IDs or mentions, comma separated",
                component=self.roles,
            )
        )
        self.add_item(ui.Label(text="Message (optional)", component=self.message))
        self.add_item(ui.Label(text="URL (link buttons only)", component=self.url))

    async def on_submit(self, interaction: discord.Interaction):
        if not await self._author_ok(interaction):
            return

        kind = self.action.values[0] if self.action.values else "link"
        label = self.label.value.strip()
        message = self.message.value.strip()
        url = self.url.value.strip()

        try:
            if kind == "link":
                button = normalize_button(
                    {"label": label, "url": url, "id": new_button_id()}
                )
            elif kind in ("role_add", "role_remove", "role_toggle"):
                role_ids = _parse_role_ids(self.roles.value)
                if not role_ids:
                    raise CV2Error("That action needs at least one role ID or mention.")
                button = normalize_button(
                    {
                        "label": label,
                        "style": ACTION_STYLES[kind],
                        "id": new_button_id(),
                        "actions": [{"type": kind, "role_ids": role_ids}],
                    }
                )
            elif kind in ("dm", "ephemeral"):
                if not message:
                    raise CV2Error("That action needs a message.")
                button = normalize_button(
                    {
                        "label": label,
                        "style": ACTION_STYLES[kind],
                        "id": new_button_id(),
                        "actions": [{"type": kind, "message": message}],
                    }
                )
            else:  # channel_message needs a channel, asked on the next step
                if not message:
                    raise CV2Error("That action needs a message.")
                view = ChannelPickView(self.builder, label, message)
                await interaction.response.send_message(
                    "Pick the channel this button should post in.",
                    view=view,
                    ephemeral=True,
                )
                return
        except CV2Error as exc:
            await interaction.response.send_message(str(exc), ephemeral=True)
            return

        self.builder.add_button(button)
        await self._finish(interaction)


class ChannelPickView(ui.View):
    def __init__(self, builder: "CV2Builder", label: str, message: str):
        super().__init__(timeout=180)
        self.builder = builder
        self.label = label
        self.message = message
        self.select = ui.ChannelSelect(
            placeholder="Choose a channel",
            channel_types=[discord.ChannelType.text, discord.ChannelType.news],
        )
        self.select.callback = self.pick
        self.add_item(self.select)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.builder.ctx.author.id:
            await interaction.response.send_message(
                "This builder belongs to someone else.", ephemeral=True
            )
            return False
        return True

    async def pick(self, interaction: discord.Interaction):
        channel = self.builder.bot.get_channel(self.select.values[0].id)
        if channel is None:
            await interaction.response.edit_message(
                content="I can't see that channel.", view=None
            )
            return
        try:
            button = normalize_button(
                {
                    "label": self.label,
                    "style": ACTION_STYLES["channel_message"],
                    "id": new_button_id(),
                    "actions": [
                        {
                            "type": "channel_message",
                            "channel_id": channel.id,
                            "message": self.message,
                        }
                    ],
                }
            )
        except CV2Error as exc:
            await interaction.response.edit_message(content=str(exc), view=None)
            return
        self.builder.add_button(button)
        await interaction.response.edit_message(
            content=f"Button added — it will post in {channel.mention}.", view=None
        )
        await self.builder.refresh()


class SaveNameModal(_BuilderModal):
    def __init__(self, builder: "CV2Builder"):
        super().__init__(builder, title="Save Components V2 layout")
        self.name = ui.TextInput(
            placeholder="welcome-1",
            max_length=60,
            required=True,
        )
        self.add_item(
            ui.Label(
                text="Template name",
                description="Reuse it later with `cv2 send <name>` or in `greet`.",
                component=self.name,
            )
        )

    async def on_submit(self, interaction: discord.Interaction):
        if not await self._author_ok(interaction):
            return
        await self.builder.save_as(interaction, self.name.value.strip())


class OverwriteView(ui.View):
    def __init__(self, builder: "CV2Builder", name: str):
        super().__init__(timeout=120)
        self.builder = builder
        self.name = name

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.builder.ctx.author.id:
            await interaction.response.send_message(
                "This builder belongs to someone else.", ephemeral=True
            )
            return False
        return True

    @ui.button(label="Overwrite", style=discord.ButtonStyle.danger)
    async def overwrite(self, interaction: discord.Interaction, button: ui.Button):
        await interaction.response.edit_message(
            content=f"Overwriting `{self.name}`…", view=None
        )
        await self.builder.persist(interaction, self.name, force=True)

    @ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: ui.Button):
        await interaction.response.edit_message(content="Save cancelled.", view=None)


class BlockActionsView(ui.View):
    """Controls for one block: edit, remove, and move it inside the container."""

    def __init__(self, builder: "CV2Builder", index: int):
        super().__init__(timeout=120)
        self.builder = builder
        self.index = index
        kind = builder.data["blocks"][index]["type"]

        if kind in ("text", "section", "gallery", "menu"):
            edit = ui.Button(label="Edit", style=discord.ButtonStyle.primary)
            edit.callback = self.edit_block
            self.add_item(edit)

        remove = ui.Button(label="Remove", style=discord.ButtonStyle.danger)
        remove.callback = self.remove_block
        self.add_item(remove)

        total = len(builder.data["blocks"])
        up = ui.Button(
            label="Move up", style=discord.ButtonStyle.secondary, disabled=index <= 0
        )
        up.callback = self.move_up
        self.add_item(up)

        down = ui.Button(
            label="Move down",
            style=discord.ButtonStyle.secondary,
            disabled=index >= total - 1,
        )
        down.callback = self.move_down
        self.add_item(down)

        cancel = ui.Button(label="Close", style=discord.ButtonStyle.secondary)
        cancel.callback = self.close
        self.add_item(cancel)

    def status(self) -> str:
        total = len(self.builder.data["blocks"])
        return f"Managing block `{self.index + 1:02d}` of `{total:02d}`."

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.builder.ctx.author.id:
            await interaction.response.send_message(
                "This builder belongs to someone else.", ephemeral=True
            )
            return False
        return True

    async def _shift(self, interaction: discord.Interaction, offset: int) -> None:
        """Swap this block with its neighbour and keep the panel on the same block."""
        blocks = self.builder.data["blocks"]
        if not 0 <= self.index < len(blocks):
            # The panel is stale: the block was removed somewhere else.
            await interaction.response.edit_message(
                content="That block no longer exists.", view=None
            )
            return

        target = self.index + offset
        if not 0 <= target < len(blocks):
            # Already at the top or bottom; just re-render with fresh button states.
            await interaction.response.edit_message(
                content=self.status(), view=BlockActionsView(self.builder, self.index)
            )
            return

        blocks[self.index], blocks[target] = blocks[target], blocks[self.index]
        self.index = target

        await self.builder.refresh()
        await interaction.response.edit_message(
            content=self.status(), view=BlockActionsView(self.builder, self.index)
        )

    async def move_up(self, interaction: discord.Interaction):
        await self._shift(interaction, -1)

    async def move_down(self, interaction: discord.Interaction):
        await self._shift(interaction, 1)

    async def edit_block(self, interaction: discord.Interaction):
        kind = self.builder.data["blocks"][self.index]["type"]
        if kind == "text":
            await interaction.response.send_modal(TextBlockModal(self.builder, self.index))
        elif kind == "section":
            await interaction.response.send_modal(SectionBlockModal(self.builder, self.index))
        elif kind == "gallery":
            await interaction.response.send_modal(GalleryBlockModal(self.builder, self.index))
        elif kind == "menu":
            await interaction.response.send_modal(MenuBlockModal(self.builder, self.index))

    async def remove_block(self, interaction: discord.Interaction):
        self.builder.data["blocks"].pop(self.index)
        await interaction.response.edit_message(content="Block removed.", view=None)
        await self.builder.refresh()

    async def close(self, interaction: discord.Interaction):
        await interaction.response.edit_message(view=None)


# ── Builder ───────────────────────────────────────────────────────────────────


class CV2Builder(ui.LayoutView):
    def __init__(
        self,
        ctx: commands.Context,
        data: dict[str, Any] | None = None,
        *,
        name: str | None = None,
        template_id: int | None = None,
    ):
        super().__init__(timeout=900)
        self.ctx = ctx
        self.bot = ctx.bot
        self.data = data or empty_template()
        self.name = name
        self.template_id = template_id
        self.target_channel_id: int | None = None
        self.message: discord.Message | None = None
        self.rebuild()

    # -- state helpers ------------------------------------------------------

    def add_button(self, button: dict[str, Any]) -> None:
        blocks = self.data["blocks"]
        if blocks and blocks[-1]["type"] == "row" and len(blocks[-1]["buttons"]) < 5:
            blocks[-1]["buttons"].append(button)
        else:
            blocks.append({"type": "row", "buttons": [button]})

    def summary(self) -> str:
        lines: list[str] = []
        lines.append(f"**Name:** `{self.name}`" if self.name else "**Name:** *not saved yet*")
        accent = self.data.get("accent_color")
        lines.append(f"**Accent:** `#{accent:06X}`" if isinstance(accent, int) else "**Accent:** default")
        lines.append(f"**Blocks:** {len(self.data['blocks'])}/{MAX_BLOCKS}")
        if not self.data["blocks"]:
            lines.append("")
            lines.append("*No blocks yet — add one with the menu below.*")
        else:
            lines.append("")
            for index, block in enumerate(self.data["blocks"], start=1):
                lines.append(f"`{index:02d}` {_short(describe_block(block), 70)}")
        text = "\n".join(lines)
        return text if len(text) <= 3800 else text[:3790] + "…"

    def rebuild(self) -> None:
        self.clear_items()
        container = ui.Container(accent_color=self.data.get("accent_color"))
        container.add_item(ui.TextDisplay("# Components V2 builder"))
        container.add_item(ui.TextDisplay(self.summary()))

        add = ui.Select(
            placeholder="Add a block…",
            options=[
                discord.SelectOption(label="Text", value="text", description="Markdown text"),
                discord.SelectOption(label="Separator", value="separator"),
                discord.SelectOption(
                    label="Section", value="section", description="Text with a thumbnail"
                ),
                discord.SelectOption(
                    label="Image gallery", value="gallery", description="Up to 10 images"
                ),
                discord.SelectOption(
                    label="Button", value="button", description="Link or role/action button"
                ),
                discord.SelectOption(
                    label="Menu",
                    value="menu",
                    description="Dropdown with emoji + description options",
                ),
                discord.SelectOption(label="Accent color", value="accent"),
                discord.SelectOption(label="Clear all blocks", value="clear"),
            ],
        )
        add.callback = self.add_block
        container.add_item(ui.ActionRow(add))

        if self.data["blocks"]:
            manage = ui.Select(
                placeholder="Manage a block…",
                options=[
                    discord.SelectOption(
                        label=_short(f"{index:02d} · {describe_block(block)}", 100),
                        value=str(index - 1),
                    )
                    for index, block in enumerate(self.data["blocks"], start=1)
                ],
            )
            manage.callback = self.manage_block
            container.add_item(ui.ActionRow(manage))

        channel_select = ui.ChannelSelect(
            placeholder="Send target channel (defaults to here)",
            channel_types=[discord.ChannelType.text, discord.ChannelType.news],
        )
        channel_select.callback = self.pick_channel
        container.add_item(ui.ActionRow(channel_select))

        variables = ui.Button(label="Variables", style=discord.ButtonStyle.secondary)
        variables.callback = self.show_variables
        preview = ui.Button(label="Preview", style=discord.ButtonStyle.secondary)
        preview.callback = self.preview_layout
        save = ui.Button(label="Save", style=discord.ButtonStyle.success)
        save.callback = self.save_layout
        send = ui.Button(label="Send", style=discord.ButtonStyle.primary)
        send.callback = self.send_layout
        cancel = ui.Button(label="Close", style=discord.ButtonStyle.danger)
        cancel.callback = self.close_builder
        container.add_item(ui.ActionRow(variables, preview, save, send, cancel))

        self.add_item(container)

    async def refresh(self) -> None:
        self.rebuild()
        if self.message is not None:
            try:
                await self.message.edit(view=self)
            except discord.HTTPException:
                pass

    # -- callbacks ----------------------------------------------------------

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.ctx.author.id:
            await interaction.response.send_message(
                "This builder belongs to someone else.", ephemeral=True
            )
            return False
        return True

    async def add_block(self, interaction: discord.Interaction):
        choice = interaction.data["values"][0] if interaction.data else ""

        if choice == "text":
            await interaction.response.send_modal(TextBlockModal(self))
        elif choice == "section":
            await interaction.response.send_modal(SectionBlockModal(self))
        elif choice == "gallery":
            await interaction.response.send_modal(GalleryBlockModal(self))
        elif choice == "button":
            await interaction.response.send_modal(ButtonModal(self))
        elif choice == "menu":
            await interaction.response.send_modal(MenuBlockModal(self))
        elif choice == "accent":
            await interaction.response.send_modal(AccentModal(self))
        elif choice == "separator":
            self.data["blocks"].append(
                {"type": "separator", "divider": True, "spacing": "small"}
            )
            self.rebuild()
            await interaction.response.edit_message(view=self)
        elif choice == "clear":
            self.data["blocks"].clear()
            self.rebuild()
            await interaction.response.edit_message(view=self)

    async def manage_block(self, interaction: discord.Interaction):
        try:
            index = int(interaction.data["values"][0])
        except (KeyError, IndexError, TypeError, ValueError):
            return
        if not 0 <= index < len(self.data["blocks"]):
            await interaction.response.send_message(
                "That block no longer exists.", ephemeral=True
            )
            return
        panel = BlockActionsView(self, index)
        await interaction.response.send_message(
            panel.status(), view=panel, ephemeral=True
        )

    async def pick_channel(self, interaction: discord.Interaction):
        try:
            channel_id = int(interaction.data["values"][0])
        except (KeyError, IndexError, TypeError, ValueError):
            return
        channel = self.bot.get_channel(channel_id)
        if channel is None:
            await interaction.response.send_message(
                "I can't see that channel.", ephemeral=True
            )
            return
        self.target_channel_id = channel.id
        await interaction.response.send_message(
            f"Layouts will be sent to {channel.mention}.", ephemeral=True
        )

    def placeholders(self, channel: Any = None):
        return build_variables(
            user=self.ctx.author,
            guild=self.ctx.guild,
            channel=channel or self.target_channel_id or self.ctx.channel,
            bot=self.bot,
            prefix=getattr(self.ctx, "prefix", None),
        )

    async def show_variables(self, interaction: discord.Interaction):
        await interaction.response.send_message(
            embed=variables_embed(0), view=VariablesView(interaction.user), ephemeral=True
        )

    async def preview_layout(self, interaction: discord.Interaction):
        try:
            view = build_view(self.data, placeholders=self.placeholders())
        except CV2Error as exc:
            await interaction.response.send_message(str(exc), ephemeral=True)
            return
        await interaction.response.send_message(view=view, ephemeral=True)

    async def save_layout(self, interaction: discord.Interaction):
        if self.name:
            await self.save_as(interaction, self.name)
        else:
            await interaction.response.send_modal(SaveNameModal(self))

    async def save_as(self, interaction: discord.Interaction, name: str) -> None:
        if not self.data["blocks"]:
            await interaction.response.send_message(
                "Add at least one block before saving.", ephemeral=True
            )
            return

        existing = await get_template(self.ctx.guild.id, name)
        if existing and existing["id"] != self.template_id:
            await interaction.response.send_message(
                f"A layout named `{existing['name']}` already exists. Overwrite it?",
                view=OverwriteView(self, existing["name"]),
                ephemeral=True,
            )
            return

        await self.persist(interaction, name, force=True)

    async def persist(
        self, interaction: discord.Interaction, name: str, *, force: bool = False
    ) -> None:
        try:
            template_id, created = await save_template(
                self.ctx.guild.id, name, self.data, self.ctx.author.id
            )
        except CV2Error as exc:
            if interaction.response.is_done():
                await interaction.followup.send(str(exc), ephemeral=True)
            else:
                await interaction.response.send_message(str(exc), ephemeral=True)
            return

        self.template_id = template_id
        self.name = name
        await self.refresh()

        text = f"{TICK} Saved as `{name}`." if created else f"{TICK} Updated `{name}`."
        if interaction.response.is_done():
            await interaction.followup.send(text, ephemeral=True)
        else:
            await interaction.response.send_message(text, ephemeral=True)

    async def send_layout(self, interaction: discord.Interaction):
        if not self.data["blocks"]:
            await interaction.response.send_message(
                "Add at least one block first.", ephemeral=True
            )
            return

        if not self.template_id:
            if not self.name:
                await interaction.response.send_message(
                    "Save the layout first (buttons keep working because of the saved template).",
                    ephemeral=True,
                )
                return
            try:
                self.template_id, _ = await save_template(
                    self.ctx.guild.id, self.name, self.data, self.ctx.author.id
                )
            except CV2Error as exc:
                await interaction.response.send_message(str(exc), ephemeral=True)
                return

        channel = self.bot.get_channel(self.target_channel_id or self.ctx.channel.id)
        if channel is None:
            await interaction.response.send_message(
                "I can't see the target channel.", ephemeral=True
            )
            return
        try:
            view = build_view(
                self.data,
                template_id=self.template_id,
                placeholders=self.placeholders(channel),
            )
            await channel.send(view=view)
        except CV2Error as exc:
            await interaction.response.send_message(str(exc), ephemeral=True)
            return
        except discord.Forbidden:
            await interaction.response.send_message(
                f"I can't send messages in {channel.mention}.", ephemeral=True
            )
            return
        await interaction.response.send_message(
            f"{TICK} Sent to {channel.mention}.", ephemeral=True
        )

    async def close_builder(self, interaction: discord.Interaction):
        self.stop()
        view = ui.LayoutView(timeout=None)
        view.add_item(
            ui.Container(
                ui.TextDisplay("# Builder closed"),
                ui.TextDisplay("Saved layouts stay available with `cv2 list`."),
            )
        )
        await interaction.response.edit_message(view=view)

    async def on_timeout(self):
        if self.message is None:
            return
        try:
            view = ui.LayoutView(timeout=None)
            view.add_item(
                ui.Container(
                    ui.TextDisplay("# Builder timed out"),
                    ui.TextDisplay("Run `cv2 build` again to continue."),
                )
            )
            await self.message.edit(view=view)
        except discord.HTTPException:
            pass


# ── Cog ───────────────────────────────────────────────────────────────────────


class CV2Messages(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def cog_load(self):
        await init_db()

    @commands.hybrid_group(
        name="cv2",
        invoke_without_command=True,
        help="Build and send Components V2 layouts.",
    )
    @blacklist_check()
    @ignore_check()
    @commands.has_permissions(manage_guild=True)
    async def cv2(self, ctx: commands.Context):
        if ctx.invoked_subcommand is None:
            await self.open_builder(ctx)

    async def open_builder(self, ctx: commands.Context, name: str | None = None) -> None:
        if ctx.guild is None:
            await ctx.send("Components V2 layouts are server-only.")
            return

        data = None
        template_id = None
        if name:
            row = await get_template(ctx.guild.id, name)
            if row is None:
                await ctx.send(
                    embed=discord.Embed(
                        description=f"No layout named `{name}`. Use `{ctx.prefix}cv2 list`.",
                        color=RED,
                    )
                )
                return
            data = row["data"]
            template_id = row["id"]
            name = row["name"]

        builder = CV2Builder(ctx, data, name=name, template_id=template_id)
        builder.message = await ctx.send(view=builder)

    @cv2.command(name="build", help="Create a layout, or edit one by name.")
    @blacklist_check()
    @ignore_check()
    @commands.has_permissions(manage_guild=True)
    @app_commands.autocomplete(name=template_autocomplete)
    async def cv2_build(self, ctx: commands.Context, *, name: Optional[str] = None):
        await self.open_builder(ctx, name)

    @cv2.command(name="send", help="Send a saved layout to a channel.")
    @blacklist_check()
    @ignore_check()
    @commands.has_permissions(manage_messages=True)
    @app_commands.autocomplete(name=template_autocomplete)
    async def cv2_send(
        self,
        ctx: commands.Context,
        name: str,
        channel: Optional[discord.TextChannel] = None,
    ):
        row = await get_template(ctx.guild.id, name)
        if row is None:
            return await ctx.send(
                embed=discord.Embed(description=f"No layout named `{name}`.", color=RED)
            )
        channel = channel or ctx.channel
        placeholders = build_variables(
            user=ctx.author, guild=ctx.guild, channel=channel, bot=self.bot, prefix=ctx.prefix
        )
        try:
            view = build_view(row["data"], template_id=row["id"], placeholders=placeholders)
            await channel.send(view=view)
        except CV2Error as exc:
            return await ctx.send(embed=discord.Embed(description=str(exc), color=RED))
        except discord.Forbidden:
            return await ctx.send(
                embed=discord.Embed(
                    description=f"I can't send messages in {channel.mention}.", color=RED
                )
            )
        if channel.id != ctx.channel.id:
            await ctx.send(f"{TICK} Sent `{row['name']}` to {channel.mention}.")

    @cv2.command(name="preview", help="Privately preview a saved layout.")
    @blacklist_check()
    @ignore_check()
    @commands.has_permissions(manage_messages=True)
    @app_commands.autocomplete(name=template_autocomplete)
    async def cv2_preview(self, ctx: commands.Context, *, name: str):
        row = await get_template(ctx.guild.id, name)
        if row is None:
            return await ctx.send(
                embed=discord.Embed(description=f"No layout named `{name}`.", color=RED)
            )
        placeholders = build_variables(
            user=ctx.author, guild=ctx.guild, channel=ctx.channel, bot=self.bot, prefix=ctx.prefix
        )
        try:
            view = build_view(row["data"], template_id=row["id"], placeholders=placeholders)
        except CV2Error as exc:
            return await ctx.send(embed=discord.Embed(description=str(exc), color=RED))
        await ctx.send(view=view, ephemeral=True)

    @cv2.command(name="list", help="List saved layouts.")
    @blacklist_check()
    @ignore_check()
    @commands.has_permissions(manage_guild=True)
    async def cv2_list(self, ctx: commands.Context):
        rows = await list_templates(ctx.guild.id)
        if not rows:
            return await ctx.send(
                embed=discord.Embed(
                    description=f"No layouts yet. Run `{ctx.prefix}cv2 build` to create one.",
                    color=RED,
                )
            )

        embed = discord.Embed(
            title=f"Components V2 layouts · {ctx.guild.name}",
            description=f"{len(rows)} saved layout(s). Use `{ctx.prefix}cv2 send <name>`.",
            color=RED,
        )
        for row in rows[:20]:
            blocks = len(row["data"].get("blocks", []))
            embed.add_field(
                name=row["name"],
                value=f"{blocks} block(s) · updated {row['updated_at'][:16].replace('T', ' ')}",
                inline=True,
            )
        if len(rows) > 20:
            embed.set_footer(text=f"Showing 20 of {len(rows)} layouts.")
        await ctx.send(embed=embed)

    @cv2.command(name="edit", help="Open a saved layout in the builder.")
    @blacklist_check()
    @ignore_check()
    @commands.has_permissions(manage_guild=True)
    @app_commands.autocomplete(name=template_autocomplete)
    async def cv2_edit(self, ctx: commands.Context, *, name: str):
        await self.open_builder(ctx, name)

    @cv2.command(name="delete", help="Delete a saved layout.")
    @blacklist_check()
    @ignore_check()
    @commands.has_permissions(manage_guild=True)
    @app_commands.autocomplete(name=template_autocomplete)
    async def cv2_delete(self, ctx: commands.Context, *, name: str):
        row = await get_template(ctx.guild.id, name)
        if row is None:
            return await ctx.send(
                embed=discord.Embed(description=f"No layout named `{name}`.", color=RED)
            )

        view = ConfirmDeleteView(self, ctx, row["name"])
        await ctx.send(
            embed=discord.Embed(
                title="Delete layout?",
                description=(
                    f"`{row['name']}` will be removed from saved layouts.\n"
                    "Messages already sent with it stay in place, but their action buttons stop working."
                ),
                color=RED,
            ),
            view=view,
        )

    @cv2.command(name="export", help="Download a saved layout as JSON.")
    @blacklist_check()
    @ignore_check()
    @commands.has_permissions(manage_guild=True)
    @app_commands.autocomplete(name=template_autocomplete)
    async def cv2_export(self, ctx: commands.Context, *, name: str):
        row = await get_template(ctx.guild.id, name)
        if row is None:
            return await ctx.send(
                embed=discord.Embed(description=f"No layout named `{name}`.", color=RED)
            )
        payload = json.dumps(row["data"], indent=2, ensure_ascii=False)
        file = discord.File(
            io.BytesIO(payload.encode("utf-8")),
            filename=f"{re.sub(r'[^A-Za-z0-9_-]+', '-', row['name'])}.cv2.json",
        )
        await ctx.send(file=file)

    @cv2.command(name="import", help="Upload a layout from JSON.")
    @blacklist_check()
    @ignore_check()
    @commands.has_permissions(manage_guild=True)
    async def cv2_import(
        self,
        ctx: commands.Context,
        name: str,
        attachment: Optional[discord.Attachment] = None,
    ):
        if attachment is None:
            if ctx.message.attachments:
                attachment = ctx.message.attachments[0]
            else:
                return await ctx.send(
                    embed=discord.Embed(
                        description="Attach a `.json` layout, or reply to one.", color=RED
                    )
                )
        if attachment.size > 512 * 1024:
            return await ctx.send(
                embed=discord.Embed(description="That file is too large.", color=RED)
            )

        raw = await attachment.read()
        try:
            data = normalize_template(json.loads(raw.decode("utf-8")))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return await ctx.send(
                embed=discord.Embed(description="That file is not valid JSON.", color=RED)
            )
        except CV2Error as exc:
            return await ctx.send(embed=discord.Embed(description=str(exc), color=RED))

        existing = await get_template(ctx.guild.id, name)
        if existing:
            return await ctx.send(
                embed=discord.Embed(
                    description=f"`{name}` already exists. Delete it or choose another name.",
                    color=RED,
                )
            )

        try:
            _, created = await save_template(ctx.guild.id, name, data, ctx.author.id)
        except CV2Error as exc:
            return await ctx.send(embed=discord.Embed(description=str(exc), color=RED))

        await ctx.send(
            f"{TICK} Imported `{name}` ({len(data['blocks'])} block(s))."
            if created
            else f"{TICK} Updated `{name}`."
        )

    async def handle_menu_select(
        self, interaction: discord.Interaction, custom_id: str
    ) -> None:
        """Acknowledge a CV2 dropdown selection.

        Menus are display components: they have no bot-side effect, so the
        selection is confirmed privately instead of staying unanswered (which
        Discord would show as "This interaction failed").
        """
        parts = custom_id.split(":")
        if len(parts) != 3:
            return
        _, template_id, menu_id = parts
        try:
            template_id = int(template_id)
        except ValueError:
            return

        values = list((interaction.data or {}).get("values") or [])
        if not values:
            return

        row = await get_template_by_id(template_id, interaction.guild_id)
        if row is None:
            await interaction.response.send_message(
                "This layout no longer exists, so its menu is inactive.", ephemeral=True
            )
            return

        menu = None
        for block in row["data"].get("blocks", []):
            if block.get("type") == "menu" and block.get("id") == menu_id:
                menu = block
                break

        if menu is None:
            await interaction.response.send_message(
                "That menu is no longer part of this layout.", ephemeral=True
            )
            return

        picked = [
            option for option in menu["options"] if option.get("value") in values
        ][:1 if menu.get("max_values", 1) == 1 else None]
        if not picked:
            await interaction.response.send_message(
                "That option is no longer part of this layout.", ephemeral=True
            )
            return

        if len(picked) == 1:
            note = f"You picked **{picked[0]['label']}**"
            if picked[0].get("description"):
                note += f" — {picked[0]['description']}"
        else:
            note = "You picked " + ", ".join(f"**{o['label']}**" for o in picked)
        await interaction.response.send_message(note, ephemeral=True)

    @commands.Cog.listener()
    async def on_interaction(self, interaction: discord.Interaction):
        if interaction.type is not discord.InteractionType.component:
            return
        custom_id = (interaction.data or {}).get("custom_id") or ""

        if custom_id.startswith("cv2m:"):
            await self.handle_menu_select(interaction, custom_id)
            return

        if not custom_id.startswith("cv2:"):
            return

        parts = custom_id.split(":")
        if len(parts) != 3:
            return
        _, template_id, button_id = parts
        try:
            template_id = int(template_id)
        except ValueError:
            return

        row = await get_template_by_id(template_id, interaction.guild_id)
        if row is None:
            await interaction.response.send_message(
                "This layout no longer exists, so its buttons are inactive.", ephemeral=True
            )
            return

        button = None
        for block in row["data"].get("blocks", []):
            if block.get("type") != "row":
                continue
            for candidate in block.get("buttons", []):
                if candidate.get("id") == button_id:
                    button = candidate
                    break
            if button:
                break

        if button is None:
            await interaction.response.send_message(
                "That button is no longer part of this layout.", ephemeral=True
            )
            return

        try:
            await execute_button(interaction, button, template_name=row["name"])
        except Exception:
            if not interaction.response.is_done():
                await interaction.response.send_message(
                    "Something went wrong while handling that button.", ephemeral=True
                )


class ConfirmDeleteView(ui.View):
    def __init__(self, cog: CV2Messages, ctx: commands.Context, name: str):
        super().__init__(timeout=120)
        self.cog = cog
        self.ctx = ctx
        self.name = name

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.ctx.author.id:
            await interaction.response.send_message(
                "Only the command author can confirm this.", ephemeral=True
            )
            return False
        return True

    @ui.button(label="Delete", style=discord.ButtonStyle.danger)
    async def confirm(self, interaction: discord.Interaction, button: ui.Button):
        deleted = await delete_template(self.ctx.guild.id, self.name)
        text = (
            f"{TICK} Deleted `{self.name}`."
            if deleted
            else f"{CROSS} `{self.name}` was already gone."
        )
        await interaction.response.edit_message(
            embed=discord.Embed(description=text, color=RED), view=None
        )

    @ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: ui.Button):
        await interaction.response.edit_message(
            embed=discord.Embed(description="Delete cancelled.", color=RED), view=None
        )
