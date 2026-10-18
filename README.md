# Discord Bot Base

A feature-rich `discord.py` v2 bot: Components V2 message layouts, moderation,
giveaways, join-to-create voice, leveling, invites and self-service role panels.

This is a stripped base built from the ZyroX CV2 layout, with four things removed:

- **No web dashboard.** The Next.js app, the FastAPI backend it talked to, and the
  Cloudflare tunnel that exposed it are all gone. The bot is a single process.
- **No antinuke module.** The anti-ban/kick/prune/webhook cogs, the whitelist and
  extra-owner commands, night mode, and their database are removed.
- **No ticket, reaction-role, music or automod modules.** The ticket system, the
  reaction-role panels, the Lavalink music stack and the automod cogs are gone.
  Components V2 buttons cover the self-service role use case.
- **No hardcoded branding.** Every avatar and name shown to users comes from the
  running bot account — its own pfp and its own Discord name.

---

## Requirements

| Requirement | Notes |
|---|---|
| Python | 3.10 or higher |
| discord.py | 2.6 or higher (pinned in `requirements.txt`) — Components V2 needs it |
| Discord bot token | with the privileged **Server Members** and **Message Content** intents |
| FFmpeg | only if you use the voice-related utility commands |

## Setup

```bash
cd bot
python -m venv .venv
source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements.txt

cp .env.example .env           # then fill it in
python main.py
```

The SQLite databases under `bot/db/` are created automatically on first start.

## Environment

Copy `bot/.env.example` to `bot/.env`.

| Variable | Default | Description |
|---|---|---|
| `TOKEN` | — | **Required.** Discord bot token |
| `OWNER_IDS` | `631428835405070347` | Comma-separated Discord user IDs with owner access. Falls back to this deployment's owner (amrcooked) in `utils/config.py` |
| `OWNER_NAME` | `amrcooked` | Owner's Discord username, used where only an ID is available |
| `brand_name` | `Bot` | Fallback name used where the client isn't available yet. Live output uses the bot account's own name |
| `SUPPORT_SERVER` | _empty_ | Optional invite URL. When unset, support links are omitted instead of pointing somewhere wrong |
| `SUPPORT_CHANNEL` | _empty_ | Optional channel link shown in a few embeds |
| `EMOJI_SYNC` | `true` | Upload/patch application emojis from `utils/emoji.py` on startup |
| `WEBHOOK_URL` | — | Optional command-log webhook |
| `CMD_WEBHOOK_URL` | — | Optional command-completion webhook |
| `SERVER_COUNT_CHANNEL_ID` | _unset_ | Optional voice channel renamed to the guild count |
| `USER_COUNT_CHANNEL_ID` | _unset_ | Optional voice channel renamed to the user count |
| `LOG_CHANNEL_ID` | _unset_ | Optional channel that logs when the bot is added to a server |

No paid tier or external service is required: the bot runs with only a token.

## Variables

Everywhere text is shown — embeds, Components V2 layouts, welcome messages and
button replies — you can use `{placeholders}`. The names follow
[Mimu's variable catalog](https://docs.mimu.bot/autoresponder/resources/variables-placeholders-and-functions),
so anything written for Mimu works here. Tokens are case-insensitive and unknown
tokens are left untouched.

| Group | Variables |
|---|---|
| User | `{user}` (mention) · `{user_name}` · `{user_displayname}` · `{user_nick}` · `{user_tag}` · `{user_id}` · `{user_avatar}` · `{user_displaycolor}` · `{user_joindate}` · `{user_createdate}` · `{user_boostsince}` |
| Server | `{server_name}` · `{server_id}` · `{server_icon}` · `{server_banner}` · `{server_membercount}` (+`_ordinal`) · `{server_membercount_nobots}` (+`_ordinal`) · `{server_botcount}` (+`_ordinal`) · `{server_rolecount}` · `{server_channelcount}` · `{server_owner}` · `{server_owner_id}` · `{server_createdate}` · `{server_randommember}` · `{server_randommember_tag}` · `{server_randommember_nobots}` |
| Boosts | `{server_boostlevel}` · `{server_boostcount}` · `{server_nextboostlevel}` · `{server_nextboostlevel_required}` · `{server_nextboostlevel_until_required}` (all three return `Max Level Reached` at tier 3) |
| Channel | `{channel}` · `{channel_name}` · `{channel_id}` · `{channel_createdate}` · `{channel_topic}` |
| Bot & misc | `{bot}` · `{bot_name}` · `{bot_avatar}` · `{bot_id}` · `{server_prefix}` · `{date}` · `{time}` · `{timestamp}` · `{newline}` |
| Roles | `{role:Moderator}` or `{role:123456789012345678}` — the only token that takes an argument |

The full list is always one click away: the **Variables** button in both builders
opens a paginated reference.

**Targets.** Every user variable accepts another member, e.g.
`{user_name:123456789012345678}` or `{user_avatar:@someone}`.

**Icons and images.** Every image field is rendered through the same engine, so
`{user_avatar}`, `{server_icon}` and `{bot_avatar}` work in an embed's thumbnail,
main image, author icon and footer icon, and in CV2 section thumbnails and
galleries. `{channel}` in a welcome embed is the channel the greeting is sent to.

**Saved layouts as variables.** Any text can pull in a saved layout:

```
Welcome {user}! {embed:rules}
Intro line for {user_name}\n{cv2:join-card}
```

- `{embed:name}` attaches the saved embed called `name` (in addition to the embed a
  welcome message may already configure).
- `{cv2:name}` sends that saved Components V2 layout. A CV2 message cannot carry
  plain content, so any text around the token is shown as a text block on top of the
  container.

Both work in welcome messages (`greet setup` → simple message) and in the embed
builder's message field. A missing name is reported instead of silently dropping the
layout. Inside a Components V2 layout the tokens are not expanded — a CV2 message
cannot carry embeds — so use them in the message text that sends the layout.

> Economy variables (balance, inventory, `{pick_code}`, …) are not implemented,
> because this base has no economy system. `{message_*}` variables only resolve
> where a triggering message exists.

## Embeds

`embed` opens the interactive builder: pick a field from the menu, fill it in, and
use **Preview**, **Save**, **Variables** or **Send** (with a channel picker) in the
same message. Saved embeds are reusable and can be dropped into any text with
`{embed:<name>}`.

| Command | What it does |
|---|---|
| `embed` | Opens the builder |
| `embed build [name]` / `embed edit <name>` | Create a new embed, or edit a saved one |
| `embed send <name> [#channel]` | Sends a saved embed |
| `embed preview <name>` | Privately previews a saved embed |
| `embed list` | Lists saved embeds |
| `embed delete <name>` | Deletes a saved embed |
| `embed export <name>` / `embed import <name> <file>` | JSON round-trip |

## Components V2

Components V2 layouts are saved per server, like a template: build one, name it, and
reuse it anywhere — including welcome messages.

### Commands

| Command | What it does |
|---|---|
| `cv2` | Opens the interactive builder (the Components V2 counterpart of `embed`) |
| `cv2 build [name]` | Creates a layout, or opens an existing one for editing |
| `cv2 send <name> [#channel]` | Sends a saved layout |
| `cv2 preview <name>` | Privately previews a saved layout (buttons inert) |
| `cv2 list` | Lists saved layouts |
| `cv2 edit <name>` | Opens a saved layout in the builder |
| `cv2 delete <name>` | Deletes a saved layout (messages already sent stay, their buttons stop) |
| `cv2 export <name>` | Downloads the layout as JSON |
| `cv2 import <name> <file>` | Imports a layout from JSON |

Builder and layout management need **Manage Server**; sending needs **Manage Messages**.

### What a layout can contain

- **Text** blocks with markdown (up to 4000 characters each).
- **Separators** — divider or spacing.
- **Sections** — text with a thumbnail image.
- **Image galleries** — up to 10 images.
- **Buttons** — up to 5 per row, either native URL buttons or action buttons.
- **Menus** — a dropdown with up to 25 options, each with its own emoji and an
  optional description, single-select or multi-select.
- A container **accent color**.

Limits are enforced while editing: 20 blocks and 40 components per layout.

### Button actions

A button can run up to 10 actions, in order:

| Action | Effect |
|---|---|
| Add role / Remove role / Toggle role | Changes the member's roles (several roles at once) |
| Private reply | Shows text only to the member who clicked |
| Send a direct message | DMs the member |
| Post in a channel | Posts text in a configured channel |
| Link | Shows a configured URL to the member |

Behaviour, matching Discord's rules:

- The interaction is deferred privately exactly once, then a single private summary is sent.
- A failing action never stops the later ones, and nothing is rolled back.
- Role changes always get a private confirmation line (`ROLE ADDED` / `ROLE REMOVED`),
  listing only changes that really happened.
- Role hierarchy and the bot's **Manage Roles** permission are checked before every change.
- Options to grant `Administrator` should be handed out carefully — the bot can only
  manage roles below its own highest role.

Buttons keep working after a restart: they carry `cv2:<template id>:<button id>`
custom IDs and a single interaction listener resolves them from the database.

Menus are added from the builder's **Menu** block and written one option per line:

```text
🎉 | Giveaway | Get pinged when events start
🛠 | Support | Open a ticket
Announcements
```

The `emoji | label | description` form is flexible — the emoji and the description
can both be left out. The builder shows the dropdown live, so the emoji, label and
description can be checked before saving. Picking an option confirms the choice
privately; menus are display components and run no bot action of their own.

### Welcome messages

Welcome messages are a single text message. Run `greet setup`, enter the text, then
set the destination and preview it:

```
greet channel #welcome
greet test
```

The message uses the full variable list above and accepts `{embed:name}` /
`{cv2:name}` tokens, so saved embeds and layouts are attached from the text instead of
being picked as a separate message type. Layouts are re-rendered on every join, so
role buttons inside a welcome card work like self-service roles, and `greet test`
proves exactly what members will receive by going through the same code path as the
join listener. Sticky messages (`sticky setup`) use the same text + token format.

### Using layouts from other cogs

```python
from utils.cv2_store import build_view, get_template, send_template

row = await get_template(guild.id, "join-card")
await channel.send(view=build_view(row["data"], template_id=row["id"], placeholders=...))

# or, in one line
await send_template(channel, row["data"], template_id=row["id"], placeholders=...)
```

`utils/cv2_store.py` holds the schema, validation, rendering and storage;
`utils/cv2_actions.py` executes button actions.

## Owner commands

Owner access comes from `OWNER_IDS` (default: `631428835405070347` — amrcooked), so
every `@commands.is_owner()` command works without extra setup. One command is worth
knowing beyond that:

```text
gsetw <message id> <user id> [<user id2> ...]   # bot owner only
```

It is hidden from `help` and gated by `utils.Tools.is_bot_owner()` rather than
`commands.is_owner()`, which also lets a *guild* owner through — `gsetw` touches any
server's giveaway, so only `OWNER_IDS` qualifies. Anyone else gets no response at all,
so the command never advertises itself in a server.

`gsetw` closes a **running** giveaway and declares the given users the winners — the
message is edited to its ended state, the winners are congratulated, and the row is
cleared so the timed loop skips it. It is the escape hatch for giveaways a raid or a
broken reaction left in the wrong hands. IDs, mentions and comma-separated lists all
parse; invalid IDs are skipped and reported rather than aborting the command. Use
`gend` for a normal random draw and `greroll` to redraw an already-ended one.

## Identity

`utils/branding.py` is the single source of truth for anything user-facing:

```python
from utils.branding import bot_avatar, bot_invite, bot_name, support_link

embed.set_author(name=bot_name(ctx.bot), icon_url=bot_avatar(ctx.bot))
f"[Invite {bot_name(ctx.bot)}]({bot_invite(ctx.bot)})"
f"Need help? {support_link('Support Server')}"     # plain text when SUPPORT_SERVER is unset
```

- `bot_name(bot)` → the bot's own Discord display name (falls back to `brand_name`).
- `bot_avatar(bot)` → the bot's own avatar URL. No image is bundled or hardcoded.
- `bot_invite(bot)` → OAuth2 invite built from the bot's own client id.
- `support_link()` / `support_button()` → optional, driven by `SUPPORT_SERVER`.

`main.py` calls `branding.register(client)` at startup, so the helpers also work in
places that have no client reference (paginators, embed builders).

## Layout

```
bot/
├── main.py             entry point (client, startup, background tasks)
├── cogs/
│   ├── commands/       slash + prefix commands (includes cv2.py, Embed.py, welcome.py)
│   ├── events/         gateway listeners (includes the greet join listener)
│   ├── moderation/     ban, kick, mute, warn, lock, snipe, purge
│   └── zyrox/          help/menu cogs (command catalogs)
├── core/               client, context, cog base
├── utils/
│   ├── variables.py    the placeholder engine (Mimu-compatible names)
│   ├── varhelp.py      the paginated Variables help view
│   ├── embed_store.py  saved embeds         → db/embeds.db
│   ├── cv2_store.py    saved CV2 layouts    → db/cv2.db
│   ├── cv2_actions.py  CV2 button actions
│   ├── greet_render.py text/embed/CV2 rendering for greet and sticky messages
│   ├── branding.py     bot identity helpers
│   └── …               emojis, paginators, config
├── db/                 SQLite databases (created at runtime)
├── lang/               translations
├── data/               image assets (status cards)
└── config.yml          AI/presence configuration
```

## License and attribution

MIT — see `LICENSE`. The original copyright notice is kept because the MIT license
requires it to travel with the code; it covers the source only. The bot's own
name, avatar and links are yours to set, and the removed dashboard/antinuke/ticket/
reaction-role/music/automod code does not appear anywhere in this tree.
# amour-cv2
