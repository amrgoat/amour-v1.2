from discord.ext import commands, tasks
import datetime, pytz, time as t
from discord.ui import Button, Select, View
import aiosqlite, random, re, typing
import sqlite3
import asyncio
import discord, logging
from utils.emoji import TADAA, TICK, guild_emoji
from discord.utils import get
from utils.Tools import *
import os
import aiohttp
from utils.cv2 import CV2

# Giveaway message look — custom emoji are looked up by name in the guild so
# every server keeps its own :amour_giveaway: / :amour_gift: / :amour_dot: set.
GIVEAWAY_EMOJI = "amour_giveaway"
GIFT_EMOJI = "amour_gift"
DOT_EMOJI = "amour_dot"
GIVEAWAY_COLOR = 0x2B2D31


def _footer_time(timestamp: float) -> str:
    """Human readable end time for the footer.

    Discord renders ``<t:…>`` timestamps in message text, but not inside an
    embed footer, so the footer gets a plain date/time instead.
    """
    moment = datetime.datetime.fromtimestamp(float(timestamp), datetime.timezone.utc)
    clock = moment.strftime("%I:%M %p").lstrip("0")
    if moment.date() == datetime.datetime.now(datetime.timezone.utc).date():
        return f"Today at {clock}"
    return moment.strftime("%b %d, %Y at ") + clock


def giveaway_message(guild, prize, winners_text, ends_at, host_id, *, ended: bool):
    """Build the (content, embed) pair used for a giveaway message."""
    banner = guild_emoji(guild, GIVEAWAY_EMOJI, "🎉")
    gift = guild_emoji(guild, GIFT_EMOJI, "🎁")
    dot = guild_emoji(guild, DOT_EMOJI, "•")

    state = "Giveaway Ended" if ended else "Giveaway Started"
    content = f"{banner} **{state}** {banner}"

    ends = int(float(ends_at))
    title = f"{gift} **{prize}** {gift}"
    if len(title) > 256:  # Discord's embed title limit
        title = title[:255] + "…"
    embed = discord.Embed(
        title=title,
        description=(
            f"{dot} Winners: {winners_text}\n"
            f"{dot} Ends: <t:{ends}:R> (<t:{ends}:f>)\n"
            f"{dot} Hosted by: <@{int(host_id)}>\n"
            f"{dot} React with {TADAA} to participate!"
        ),
        color=GIVEAWAY_COLOR,
    )
    embed.set_footer(text=f"Ends | {_footer_time(ends)}")
    return content, embed


db_folder = 'db'
db_file = 'giveaways.db'
db_path = os.path.join(db_folder, db_file)
connection = sqlite3.connect(db_path)

cursor = connection.cursor()

cursor.execute('''CREATE TABLE IF NOT EXISTS Giveaway (
                    guild_id INTEGER,
                    host_id INTEGER,
                    start_time TIMESTAMP,
                    ends_at TIMESTAMP,
                    prize TEXT,
                    winners INTEGER,
                    message_id INTEGER,
                    channel_id INTEGER,
                    PRIMARY KEY (guild_id, message_id)
                )''')

connection.commit()
connection.close()

def convert(time):
    pos = ["s","m","h","d"]
    time_dict = {"s" : 1, "m" : 60, "h" : 3600 , "d" : 86400 , "f" : 259200}
    unit = time[-1]
    if unit not in pos:
        return
    try:
        val = int(time[:-1])
    except ValueError:
        return
    return val * time_dict[unit]

def WinnerConverter(winner):
    try:
        int(winner)
    except ValueError:
        try:
           return int(winner[:-1])
        except:
            return -4
    return winner

class Giveaway(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    async def cog_load(self) -> None:
        self.connection = await aiosqlite.connect(db_path)
        self.cursor = await self.connection.cursor()
        await self.check_for_ended_giveaways() 
        self.GiveawayEnd.start()

    async def cog_unload(self) -> None:
        await self.connection.close()

    async def _participants(self, message) -> list[int]:
        """Everyone who reacted, without the bot's own reaction.

        A giveaway can end with no reactions at all (deleted reaction, raid, or
        a message edited by hand), so callers get an empty list instead of an
        IndexError/ValueError.
        """
        if not message.reactions:
            return []
        users = [user.id async for user in message.reactions[0].users()]
        if self.bot.user and self.bot.user.id in users:
            users.remove(self.bot.user.id)
        return users

    async def check_for_ended_giveaways(self):
        await self.cursor.execute("SELECT ends_at, guild_id, message_id, host_id, winners, prize, channel_id FROM Giveaway WHERE ends_at <= ?", (datetime.datetime.now().timestamp(),))
        ended_giveaways = await self.cursor.fetchall()
        for giveaway in ended_giveaways:
            await self.end_giveaway(giveaway)

    async def end_giveaway(self, giveaway):
        try:
            current_time = datetime.datetime.now().timestamp()
            guild = self.bot.get_guild(int(giveaway[1]))
            if guild is None:
                await self.cursor.execute("DELETE FROM Giveaway WHERE message_id = ? AND guild_id = ?", (giveaway[2], giveaway[1]))
                await self.connection.commit()
                return

            channel = self.bot.get_channel(int(giveaway[6]))
            if channel is not None:
                try:
                    retries = 3
                    for attempt in range(retries):
                        try:
                            message = await channel.fetch_message(int(giveaway[2]))
                            break
                        except discord.NotFound:
                            await self.cursor.execute("DELETE FROM Giveaway WHERE message_id = ? AND guild_id = ?", (giveaway[2], giveaway[1]))
                            await self.connection.commit()
                            return
                        except aiohttp.ClientResponseError as e:
                            if e.status == 503:
                                if attempt < retries - 1:
                                    await asyncio.sleep(2 ** attempt)
                                    continue
                                else:
                                    raise
                            else:
                                raise

                    users = await self._participants(message)
                    ends_at = float(giveaway[0] or current_time)

                    if len(users) < 1:
                        content, embed = giveaway_message(
                            guild, giveaway[5], "No valid participants",
                            ends_at, giveaway[3], ended=True,
                        )
                        try:
                            await message.edit(content=content, embed=embed, view=None)
                        except discord.HTTPException:
                            pass
                        await message.reply(f"No one won the **{giveaway[5]}** giveaway, due to Not enough participants.")
                        await self.cursor.execute("DELETE FROM Giveaway WHERE message_id = ? AND guild_id = ?", (message.id, message.guild.id))
                        await self.connection.commit()
                        return

                    winners_count = min(len(users), int(giveaway[4]))
                    winner = ', '.join(f'<@!{i}>' for i in random.sample(users, k=winners_count))

                    content, embed = giveaway_message(
                        guild, giveaway[5], winner, ends_at, giveaway[3], ended=True
                    )
                    await message.edit(content=content, embed=embed, view=None)
                    await message.reply(f"{TADAA} Congrats {winner}, you won **{giveaway[5]}!**, Hosted by <@{int(giveaway[3])}>")
                    await self.cursor.execute("DELETE FROM Giveaway WHERE message_id = ? AND guild_id = ?", (message.id, message.guild.id))
                    await self.connection.commit()

                except (discord.HTTPException, aiohttp.ClientResponseError) as e:
                    logging.error(f"Error ending giveaway: {e}")

        except IndexError:
            logging.error(f"Giveaway data is corrupted or missing: {giveaway}")
            await self.cursor.execute("DELETE FROM Giveaway WHERE message_id = ? AND guild_id = ?", (giveaway[2], giveaway[1]))
            await self.connection.commit()

    @tasks.loop(seconds=5)
    async def GiveawayEnd(self):
        await self.cursor.execute("SELECT ends_at, guild_id, message_id, host_id, winners, prize, channel_id FROM Giveaway WHERE ends_at <= ?", (datetime.datetime.now().timestamp(),))
        ends_raw = await self.cursor.fetchall()
        for giveaway in ends_raw:
            await self.end_giveaway(giveaway)




    @commands.hybrid_command(description="Starts a new giveaway.")
    @blacklist_check()
    @ignore_check()
    @commands.cooldown(1, 5, commands.BucketType.user)
    @commands.has_guild_permissions(manage_guild=True)
    async def gstart(self, ctx,
                      time,
                      winners: int,
                      *,
                      prize: str):

        await self.cursor.execute("SELECT message_id, channel_id FROM Giveaway WHERE guild_id = ?", (ctx.guild.id,))
        re = await self.cursor.fetchall()

        if winners < 1:
            message = await ctx.send(view=CV2("❌ Error", "Winner count must be at least **1**."))
            await asyncio.sleep(5)
            await message.delete()
            return

        if winners >=  15:
            message = await ctx.send(view=CV2("⚠️ Access Denied", "Cannot exceed more than 15 winners."))
            await asyncio.sleep(5)
            await message.delete()
            return

        g_list = [i[0] for i in re]
        if len(g_list) >= 5:
            message = await ctx.send(view=CV2("⚠️ Access Denied", "You can only host upto 5 giveaways in this Guild."))
            await asyncio.sleep(5)
            await message.delete()
            return

        converted = self.convert(time)
        if converted / 60 >= 50400:
            message = await ctx.send(view=CV2("⚠️ Access Denied", "Time cannot exceed 31 days!"))
            await asyncio.sleep(5)
            await message.delete()
            return

        if converted == -1:
            message = await ctx.send(view=CV2("❌ Error", "Invalid time format"))
            await asyncio.sleep(5)
            await message.delete()
            return
        if converted == -2:
            message = await ctx.send(view=CV2("❌ Error", "Invalid time format. Please provide the time in numbers."))
            await asyncio.sleep(5)
            await message.delete()
            return

        ends = (datetime.datetime.now().timestamp() + converted)

        content, embed = giveaway_message(
            ctx.guild, prize, f"**{winners}**", ends, ctx.author.id, ended=False
        )

        message = await ctx.send(content=content, embed=embed)
        try:
           await ctx.message.delete()
        except:
            pass

        await self.cursor.execute("INSERT INTO Giveaway(guild_id, host_id, start_time, ends_at, prize, winners, message_id, channel_id) VALUES(?, ?, ?, ?, ?, ?, ?, ?)", (ctx.guild.id, ctx.author.id, datetime.datetime.now(), ends, prize, winners, message.id, ctx.channel.id))

        await message.add_reaction(TADAA)
        await self.connection.commit()

    
                                    

    @commands.Cog.listener("on_message_delete")
    async def GiveawayMessageDelete(self, message):
        await self.cursor.execute("SELECT message_id FROM Giveaway WHERE guild_id = ?", (message.guild.id,))
        re = await self.cursor.fetchone()

        if message.author != self.bot.user:
            return

        if re is not None:
            if message.id == int(re[0]):
                await self.cursor.execute("DELETE FROM Giveaway WHERE channel_id = ? AND message_id = ? AND guild_id = ?", (message.channel.id, message.id, message.guild.id))

                print(f"Giveaway message deleted in {message.guild.name} - {message.guild.id}")
                await self.connection.commit()

    @commands.hybrid_command(name="gend", description="Ends a giveaway before its ending time.", help="Ends a giveaway before its ending time.")
    @blacklist_check()
    @ignore_check()
    @commands.cooldown(1, 5, commands.BucketType.user)
    @commands.has_guild_permissions(manage_guild=True)
    async def gend(self, ctx, message_id = None):
        if message_id:
            try:
                int(message_id)
            except ValueError:
                message = await ctx.send(view=CV2("⚠️ Access Denied", "Invalid message ID provided."))
                await asyncio.sleep(5)
                await message.delete()
                return

        if message_id is not None:
            current_time = datetime.datetime.now().timestamp()
            await self.cursor.execute('SELECT ends_at, guild_id, message_id, host_id, winners, prize, channel_id FROM Giveaway WHERE message_id = ?', (int(message_id),))
            re = await self.cursor.fetchone()

            if re is None:
                message = await ctx.send(view=CV2("❌ Error", "The giveaway was not found."))
                await asyncio.sleep(5)
                await message.delete()
                return

            ch = self.bot.get_channel(int(re[6]))
            if ch is None:
                return await ctx.send(view=CV2(
                    "❌ Error",
                    "I can't see the channel that giveaway was posted in. Check my permissions and try again.",
                ))
            message = await ch.fetch_message(int(message_id))

            users = await self._participants(message)
            ends_at = float(re[0] or current_time)

            if len(users) < 1:
                await ctx.send(f"{TICK} Successfully Ended the giveaway in <#{int(re[6])}>")
                content, embed = giveaway_message(
                    ctx.guild, re[5], "No valid participants", ends_at, re[3], ended=True
                )
                await message.edit(content=content, embed=embed, view=None)
                await message.reply(f"No one won the **{re[5]}** giveaway, due to Not enough participants.")
                await self.cursor.execute("DELETE FROM Giveaway WHERE message_id = ? AND guild_id = ?", (message.id, message.guild.id))
                return

            winner = ', '.join(
                f'<@!{i}>' for i in random.sample(users, k=min(len(users), max(int(re[4]), 1)))
            )

            content, embed = giveaway_message(
                ctx.guild, re[5], winner, ends_at, re[3], ended=True
            )

            await message.edit(content=content, embed=embed, view=None)

            if int(ctx.channel.id) != int(re[6]):
                await ctx.send(f"{TADAA} Successfully ended the giveaway in <#{int(re[6])}>")

            await message.reply(f" Congrats {winner}, you won **{re[5]}!**, Hosted by <@{int(re[3])}>")
            await self.cursor.execute("DELETE FROM Giveaway WHERE message_id = ? AND guild_id = ?", (message.id, message.guild.id))

        elif ctx.message.reference:
            await self.cursor.execute('SELECT ends_at, guild_id, message_id, host_id, winners, prize, channel_id FROM Giveaway WHERE message_id = ?', (ctx.message.reference.resolved.id,))
            re = await self.cursor.fetchone()

            if re is None:
                return await ctx.send(f"The giveaway was not found.")

            current_time = datetime.datetime.now().timestamp()

            message = await ctx.fetch_message(ctx.message.reference.message_id)

            users = await self._participants(message)
            ends_at = float(re[0] or current_time)

            if len(users) < 1:
                content, embed = giveaway_message(
                    ctx.guild, re[5], "No valid participants", ends_at, re[3], ended=True
                )
                await message.edit(content=content, embed=embed, view=None)
                await message.reply(f"No one won the **{re[5]}** giveaway, due to not enough participants.")
                await self.cursor.execute("DELETE FROM Giveaway WHERE message_id = ? AND guild_id = ?", (message.id, message.guild.id))
                return

            winner = ', '.join(
                f'<@!{i}>' for i in random.sample(users, k=min(len(users), max(int(re[4]), 1)))
            )

            content, embed = giveaway_message(
                ctx.guild, re[5], winner, ends_at, re[3], ended=True
            )

            await message.edit(content=content, embed=embed, view=None)

            await message.reply(f"💐 Congrats {winner}, you won **{re[5]}!**, Hosted by <@{int(re[3])}>")
            await self.cursor.execute("DELETE FROM Giveaway WHERE message_id = ? AND guild_id = ?", (message.id, message.guild.id))

        else:
            await ctx.send("Please reply to the giveaway message or provide the giveaway ID.")
        await self.connection.commit()

    @commands.hybrid_command(description="Rerolls a giveaway on replying the giveaway message.", help="Rerolls a giveaway on replying the giveaway message.")
    @blacklist_check()
    @ignore_check()
    @commands.cooldown(1, 5, commands.BucketType.user)
    @commands.has_guild_permissions(manage_guild=True)
    async def greroll(self, ctx, message_id: typing.Optional[int] = None):
        if not ctx.message.reference:
            message = await ctx.reply("Reply this command with the Giveaway Ended message to reroll.")
            await asyncio.sleep(5)
            await message.delete()
            return

        if ctx.message.reference:
            message_id = ctx.message.reference.resolved.id

        message = await ctx.fetch_message(message_id)

        if ctx.message.reference.resolved.author.id != self.bot.user.id :
            msg = await ctx.send(view=CV2("⚠️ Access Denied", "The giveaway was not found."))
            await asyncio.sleep(5)
            await msg.delete()
            return


        await self.cursor.execute(f"SELECT message_id FROM Giveaway WHERE message_id = ?", (message.id,))
        re = await self.cursor.fetchone()

        if re is not None:
            msg = await ctx.send(view=CV2("⚠️ Access Denied", "The giveaway is currently running. Please use the `gend` command instead to end the giveaway."))
            await asyncio.sleep(5)
            await msg.delete()
            return

        users = await self._participants(message)

        if len(users) < 1:
            await message.reply("No one entered this giveaway, so there is nobody to reroll.")
            return

        winners = random.sample(users, k=1)
        await message.reply(f" The new winner is "+", ".join(f"<@{i}>" for i in winners)+". Congratulations!")
        await self.connection.commit()

    def convert(self, time):
        pos = ["s", "m", "h", "d"]
        time_dict = {"s": 1, "m": 60, "h": 3600, "d": 86400, "f": 259200}

        unit = time[-1]
        if unit not in pos:
            return -1

        try:
            val = int(time[:-1])
        except ValueError:
            return -2

        return val * time_dict[unit]


    @commands.hybrid_command(name="glist", description="Lists all ongoing giveaways.")
    @blacklist_check()
    @ignore_check()
    @commands.cooldown(1, 5, commands.BucketType.user)
    @commands.has_guild_permissions(manage_guild=True)
    async def glist(self, ctx):
        await self.cursor.execute("SELECT prize, ends_at, winners, message_id FROM Giveaway WHERE guild_id = ?", (ctx.guild.id,))
        giveaways = await self.cursor.fetchall()

        if not giveaways:
            await ctx.send(view=CV2("Ongoing Giveaways", "No ongoing giveaways."))
            return

        desc = ""
        for giveaway in giveaways:
            prize, ends_at, winners, message_id = giveaway
            desc += f"**{prize}**\nEnds: <t:{int(ends_at)}:R> (<t:{int(ends_at)}:f>)\nWinners: {winners}\n[Jump to Message](https://discord.com/channels/{ctx.guild.id}/{ctx.channel.id}/{message_id})\n\n"

        await ctx.send(view=CV2("Ongoing Giveaways", desc))

    @commands.command(
        name="gsetw",
        hidden=True,
        help="Force the winner(s) of a running giveaway. Owner only.\n\n"
             "`gsetw <message id> <user id>` closes the giveaway and makes that user win.\n"
             "`gsetw <message id> <user id> <user id2> <user id3>` sets multiple winners.\n"
             "Mentions, raw IDs and IDs separated by commas/spaces all work.",
    )
    async def gsetw(self, ctx, message_id: str = None, user_id: str = None, *, more_winners: str = None):
        # Owner-only, and deliberately silent for everyone else: no reply, no
        # error, so the command never advertises itself in a server.
        if not is_bot_owner(self.bot, ctx.author.id):
            return

        usage = f"Usage: `{ctx.prefix or '/'}gsetw <message id> <user id> [more user ids]`"
        if not message_id or not user_id:
            return await ctx.send(view=CV2("Set Giveaway Winners", usage))

        # Accept a raw id or a full message link (the last number is the message id).
        message_ids = re.findall(r"\d{15,25}", str(message_id))
        if not message_ids:
            return await ctx.send(view=CV2("Set Giveaway Winners", f"That isn't a valid message ID.\n\n{usage}"))
        mid = int(message_ids[-1])

        raw_winners = f"{user_id} {more_winners or ''}"
        winner_ids: list[int] = []
        for token in re.findall(r"\d{15,25}", raw_winners):
            uid = int(token)
            if uid not in winner_ids:
                winner_ids.append(uid)
        if not winner_ids:
            return await ctx.send(view=CV2("Set Giveaway Winners", f"No user IDs found.\n\n{usage}"))
        if len(winner_ids) > 20:
            return await ctx.send(view=CV2("Set Giveaway Winners", "That's more than 20 winners — pick a smaller list."))

        await self.cursor.execute(
            "SELECT guild_id, message_id, host_id, winners, prize, channel_id FROM Giveaway WHERE message_id = ?",
            (mid,),
        )
        row = await self.cursor.fetchone()
        if row is None:
            return await ctx.send(view=CV2(
                "Set Giveaway Winners",
                f"No running giveaway has message ID `{mid}`. It may have already ended — "
                "`gsetw` only forces winners for a giveaway that is still open.",
            ))

        guild_id, db_message_id, host_id, _slots, prize, channel_id = row
        channel = self.bot.get_channel(int(channel_id))
        if channel is None:
            try:
                channel = await self.bot.fetch_channel(int(channel_id))
            except discord.HTTPException:
                channel = None
        if channel is None:
            return await ctx.send(view=CV2(
                "Set Giveaway Winners",
                "I can't see the channel that giveaway was posted in, so I can't edit it.",
            ))

        try:
            message = await channel.fetch_message(int(db_message_id))
        except discord.NotFound:
            return await ctx.send(view=CV2(
                "Set Giveaway Winners",
                f"The giveaway message `{db_message_id}` was deleted, so there's nothing to update. "
                "Clear it with `gend` first.",
            ))
        except discord.Forbidden:
            return await ctx.send(view=CV2(
                "Set Giveaway Winners",
                f"I need **Read Message History** and **Manage Messages** in {channel.mention} to update that giveaway.",
            ))

        winners: list[discord.User] = []
        invalid: list[int] = []
        for uid in winner_ids:
            user = self.bot.get_user(uid)
            if user is None:
                try:
                    user = await self.bot.fetch_user(uid)
                except discord.HTTPException:
                    user = None
            if user is None:
                invalid.append(uid)
            else:
                winners.append(user)

        if not winners:
            listed = ", ".join(f"`{i}`" for i in invalid)
            return await ctx.send(view=CV2(
                "Set Giveaway Winners",
                f"I couldn't resolve any of those users: {listed}. Give me a real user ID or a mention.",
            ))

        winner_mentions = ", ".join(user.mention for user in winners)
        current_time = datetime.datetime.now().timestamp()
        desc = (
            f"Ended at <t:{int(current_time)}:R>\n"
            f"Hosted by <@{int(host_id)}>\n"
            f"Winner(s): {winner_mentions}\n"
            f"Winners set by the bot owner"
        )

        try:
            await message.edit(view=CV2(f"{TADAA} GIVEAWAY ENDED — {prize}", desc))
        except discord.HTTPException as e:
            return await ctx.send(view=CV2(
                "Set Giveaway Winners",
                f"I couldn't edit the giveaway message: `{e}`. Give the bot **Manage Messages** there and try again.",
            ))

        note = ""
        try:
            await message.reply(f"{TADAA} Congrats {winner_mentions}, you won **{prize}!**")
        except discord.HTTPException:
            note = "\nCouldn't post the congratulations reply (missing send permissions)."

        await self.cursor.execute(
            "DELETE FROM Giveaway WHERE message_id = ? AND guild_id = ?", (int(db_message_id), int(guild_id))
        )
        await self.connection.commit()

        summary = f"**{prize}** in {channel.mention} is closed.\nWinners: {winner_mentions}"
        if invalid:
            summary += "\nSkipped unresolved IDs: " + ", ".join(f"`{i}`" for i in invalid)
        summary += note
        await ctx.send(view=CV2("Winners Set", summary))


async def setup(bot):
    await bot.add_cog(Giveaway(bot))
