"""Execution of Components V2 actions (button clicks and menu picks).

The rules implemented here follow Discord's interaction model:

* the interaction is deferred privately exactly once, so no initial response is
  ever sent twice;
* actions run in the configured order and a failure never stops later actions;
* successful side effects are kept (there is no rollback);
* the final summary is private and only lists changes that really happened;
* role changes always get a private confirmation line, even when the action's
  own messages are silenced, naming only the roles that really changed;
  nothing changed still confirms with "Roles updated successfully.", and a
  removal that removed nothing says "Role removed: none".
"""

from __future__ import annotations

from typing import Any, Iterable

import discord

from utils.cv2_store import ROLE_ACTION_TYPES, placeholders_for, render_text

MAX_SUMMARY = 1900


def _bullets(items: list[str], limit: int = 5) -> str:
    shown = items[:limit]
    extra = len(items) - len(shown)
    text = "\n".join(f"• {item}" for item in shown)
    if extra > 0:
        text += f"\n• …and {extra} more"
    return text


def _clip(text: str) -> str:
    return text if len(text) <= MAX_SUMMARY else text[: MAX_SUMMARY - 1] + "…"


async def _run_role_action(
    interaction: discord.Interaction,
    action: dict[str, Any],
    added: list[str],
    removed: list[str],
    errors: list[str],
    notes: list[str],
) -> None:
    guild = interaction.guild
    member = interaction.user
    if (
        guild is None
        or not hasattr(member, "roles")
        or not hasattr(member, "add_roles")
        or not hasattr(member, "remove_roles")
    ):
        errors.append("Role actions only work inside a server.")
        return

    me = guild.me
    for role_id in action["role_ids"]:
        role = guild.get_role(role_id)
        if role is None:
            errors.append(f"Role `{role_id}` no longer exists.")
            continue
        if role.is_default() or role.managed:
            errors.append(f"I can't assign {role.mention}.")
            continue
        if not me.guild_permissions.manage_roles:
            errors.append("I need the **Manage Roles** permission.")
            continue
        if role >= me.top_role:
            errors.append(f"I can't manage {role.mention} — it is above my highest role.")
            continue

        has_role = role in member.roles
        try:
            if action["type"] == "role_add":
                if has_role:
                    # Already held: not listed in the summary.
                    continue
                await member.add_roles(role, reason="Components V2 button")
                added.append(role.mention)
            elif action["type"] == "role_remove":
                if not has_role:
                    # Not held: not listed in the summary.
                    continue
                await member.remove_roles(role, reason="Components V2 button")
                removed.append(role.mention)
            else:  # role_toggle
                if has_role:
                    await member.remove_roles(role, reason="Components V2 button")
                    removed.append(role.mention)
                else:
                    await member.add_roles(role, reason="Components V2 button")
                    added.append(role.mention)
        except discord.Forbidden:
            errors.append(f"Discord refused the change for {role.mention} (missing permissions).")
        except discord.HTTPException as exc:
            errors.append(f"Discord rejected the change for {role.mention} ({exc.status}).")


async def execute_actions(
    interaction: discord.Interaction,
    actions: Iterable[dict[str, Any]],
    *,
    label: str = "",
    template_name: str = "",
    heading: str | None = None,
) -> None:
    """Run every action in ``actions`` and report the outcome privately.

    ``label`` is the button label (or menu option label) used by the
    ``{button_label}`` placeholder, and ``heading`` is an optional first line
    for the private summary.
    """
    if interaction.guild is None:
        await interaction.response.send_message(
            "These components only work inside a server.", ephemeral=True
        )
        return

    await interaction.response.defer(ephemeral=True)

    prefix = None
    try:
        from utils.Tools import getConfig

        prefix = (await getConfig(interaction.guild.id)).get("prefix")
    except Exception:
        prefix = None

    base = placeholders_for(
        interaction.user,
        interaction.guild,
        interaction.channel,
        bot=getattr(interaction, "client", None),
        prefix=prefix,
    )
    base["button_label"] = label
    base["template_name"] = template_name

    added: list[str] = []
    removed: list[str] = []
    notes: list[str] = []
    errors: list[str] = []
    role_actions = 0
    role_remove_seen = False

    for action in actions:
        kind = action["type"]
        try:
            if kind in ROLE_ACTION_TYPES:
                role_actions += 1
                role_remove_seen = role_remove_seen or kind == "role_remove"
                await _run_role_action(interaction, action, added, removed, errors, notes)

            elif kind == "dm":
                text = render_text(action["message"], base)
                try:
                    await interaction.user.send(text)
                    notes.append("I sent you a direct message.")
                except discord.Forbidden:
                    errors.append("I couldn't DM you — your privacy settings block me.")

            elif kind == "ephemeral":
                notes.append(render_text(action["message"], base))

            elif kind == "channel_message":
                channel = interaction.guild.get_channel(action["channel_id"])
                if channel is None or not hasattr(channel, "send"):
                    errors.append("The configured channel no longer exists.")
                    continue
                await channel.send(render_text(action["message"], base))
                notes.append(f"Message sent to {channel.mention}.")

            elif kind == "link":
                url = render_text(action["url"], base)
                if not url.startswith(("http://", "https://")):
                    errors.append("The configured link is not a valid URL.")
                else:
                    notes.append(f"Link: {url}")
        except discord.HTTPException as exc:
            errors.append(f"`{kind}` failed (Discord error {exc.status}).")
        except Exception as exc:  # never let one action break the rest
            errors.append(f"`{kind}` failed ({type(exc).__name__}).")

    lines: list[str] = []
    if heading:
        lines.append(heading)
    # Only roles that really changed are named: a removal from a member who
    # doesn't hold most of the configured roles lists just the ones removed.
    if added:
        lines.append("Role added: " + ", ".join(added))
    if removed:
        lines.append("Role removed: " + ", ".join(removed))
    elif role_remove_seen and not added:
        lines.append("Role removed: none")
    if notes:
        lines.extend(notes)
    if errors:
        lines.append("**I couldn't complete everything:**")
        lines.append(_bullets(errors))

    # A role action that changed nothing still confirms it ran.
    if not lines and role_actions:
        lines.append("Roles updated successfully.")

    if not lines:
        return

    await interaction.followup.send(_clip("\n".join(lines)), ephemeral=True)


async def execute_button(
    interaction: discord.Interaction,
    button: dict[str, Any],
    *,
    template_name: str = "",
) -> None:
    """Run every action attached to ``button`` and report the outcome privately."""
    await execute_actions(
        interaction,
        button.get("actions") or [],
        label=button.get("label", ""),
        template_name=template_name,
    )
