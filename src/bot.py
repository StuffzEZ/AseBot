"""
AseBot — Discord bot that converts PNG images to .aseprite format.

On any image upload:
  1. Creates a thread on the message
  2. Runs default processing pipeline
  3. Posts the .aseprite + preview PNG + button panel in the thread

Users can then adjust settings via buttons and re-process.
"""

from __future__ import annotations

import asyncio
import io
import logging
import os
import traceback
from typing import Optional

import discord
from discord.ext import commands

import sessions
from processor import ProcessConfig, process_and_export
from views import ControlPanel, _cfg_summary

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger("asebot")

# ---------------------------------------------------------------------------
# Bot setup
# ---------------------------------------------------------------------------

TOKEN = os.environ.get("DISCORD_TOKEN", "")

def _parse_ids(raw: str) -> set[int]:
    return {int(x.strip()) for x in raw.split(",") if x.strip().isdigit()}

_GUILD_ID_RAW   = os.environ.get("GUILD_ID", "").strip()
_CHANNEL_ID_RAW = os.environ.get("CHANNEL_IDS", "").strip()

WATCH_GUILD_ID: Optional[int] = int(_GUILD_ID_RAW)   if _GUILD_ID_RAW.isdigit()   else None
WATCH_CHANNEL_IDS: set[int]   = _parse_ids(_CHANNEL_ID_RAW) if _CHANNEL_ID_RAW else set()

intents = discord.Intents.default()
intents.message_content = True
intents.messages = True

bot = commands.Bot(command_prefix="!", intents=intents)

# ---------------------------------------------------------------------------
# Events
# ---------------------------------------------------------------------------

@bot.event
async def on_ready():
    log.info(f"Logged in as {bot.user} ({bot.user.id})")
    if WATCH_GUILD_ID:
        log.info(f"Restricted to guild: {WATCH_GUILD_ID}")
    if WATCH_CHANNEL_IDS:
        log.info(f"Watching channels: {WATCH_CHANNEL_IDS}")
    if not WATCH_GUILD_ID and not WATCH_CHANNEL_IDS:
        log.info("Watching ALL channels in ALL servers")
    await bot.tree.sync()
    log.info("Slash commands synced")


@bot.event
async def on_message(message: discord.Message):
    if message.author.bot:
        return

    # Check guild filter
    if WATCH_GUILD_ID and message.guild and message.guild.id != WATCH_GUILD_ID:
        await bot.process_commands(message)
        return

    # Check channel filter
    if WATCH_CHANNEL_IDS and message.channel.id not in WATCH_CHANNEL_IDS:
        await bot.process_commands(message)
        return

    # Don't trigger in threads themselves (avoid loops)
    if isinstance(message.channel, discord.Thread):
        await bot.process_commands(message)
        return

    # Look for PNG/WebP/JPG attachments
    image_attachments = [
        a for a in message.attachments
        if a.content_type and any(
            a.content_type.startswith(t) for t in ("image/png", "image/webp", "image/jpeg")
        )
    ]

    if not image_attachments:
        await bot.process_commands(message)
        return

    # Process the first image
    attachment = image_attachments[0]

    try:
        await _handle_image_upload(message, attachment)
    except Exception:
        log.error("Error handling image upload:\n" + traceback.format_exc())
        try:
            await message.reply("⚠️ Something went wrong processing that image. Please try again.")
        except Exception:
            pass

    await bot.process_commands(message)


# ---------------------------------------------------------------------------
# Core image handler
# ---------------------------------------------------------------------------

async def _handle_image_upload(message: discord.Message, attachment: discord.Attachment):
    """Download image, create thread, run processing, post results."""

    # Create thread immediately so the user sees something happening
    thread_name = f"🎨 {attachment.filename[:40]}"
    thread = await message.create_thread(name=thread_name, auto_archive_duration=1440)

    async with thread.typing():
        # Download original image
        img_bytes = await attachment.read()

        # Create session with defaults
        cfg = await sessions.create_session(thread.id, img_bytes)

        await thread.send(
            f"📥 Got **{attachment.filename}** — running default processing…"
        )

        # Run processing in executor (CPU-bound)
        png_bytes, ase_bytes, info = await asyncio.get_event_loop().run_in_executor(
            None, process_and_export, img_bytes, cfg
        )

        # Post results
        await _post_results(thread, png_bytes, ase_bytes, info, cfg, attachment.filename)


async def _post_results(
    thread: discord.Thread,
    png_bytes: bytes,
    ase_bytes: bytes,
    info: dict,
    cfg: ProcessConfig,
    original_filename: str,
):
    """Post the processed files and control panel into the thread."""
    base_name = original_filename.rsplit(".", 1)[0]
    orig_w, orig_h = info["original_size"]
    final_w, final_h = info["final_size"]
    pixel_size = info.get("pixel_size", 1)
    steps_text = "\n".join(f"  • {s}" for s in info["steps"]) or "  • (no changes)"

    embed = discord.Embed(
        title="✅ Processing complete",
        color=discord.Color.green(),
    )
    embed.add_field(
        name="📊 Info",
        value=(
            f"Original: `{orig_w}×{orig_h}`\n"
            f"Output: `{final_w}×{final_h}`\n"
            f"Logical pixel: `{pixel_size}px`"
        ),
        inline=True,
    )
    embed.add_field(
        name="🔧 Steps applied",
        value=steps_text,
        inline=True,
    )
    embed.add_field(
        name="⚙️ Settings",
        value=_cfg_summary(cfg),
        inline=False,
    )
    embed.set_footer(text="Adjust settings below, then hit ▶ Process to regenerate")

    png_file = discord.File(io.BytesIO(png_bytes), filename=f"{base_name}_pixel.png")
    ase_file = discord.File(io.BytesIO(ase_bytes), filename=f"{base_name}.aseprite")

    # Set preview image in embed
    embed.set_image(url=f"attachment://{base_name}_pixel.png")

    view = ControlPanel(process_fn=_reprocess, thread_id=thread.id)

    await thread.send(
        embed=embed,
        files=[png_file, ase_file],
        view=view,
    )

    await thread.send(
        "**🎛️ Control Panel** — adjust settings then hit **▶ Process** to regenerate.",
        view=ControlPanel(process_fn=_reprocess, thread_id=thread.id),
    )


async def _reprocess(interaction: discord.Interaction, thread_id: int):
    """Re-run the pipeline with current session config and post updated results."""
    entry = await sessions.get_session(thread_id)
    if entry is None:
        await interaction.followup.send("⚠️ Session expired. Please re-upload the image.", ephemeral=True)
        return

    cfg, img_bytes = entry

    # Run in executor
    try:
        png_bytes, ase_bytes, info = await asyncio.get_event_loop().run_in_executor(
            None, process_and_export, img_bytes, cfg
        )
    except Exception as e:
        log.error(f"Reprocess error: {e}\n{traceback.format_exc()}")
        await interaction.followup.send(f"⚠️ Processing failed: {e}", ephemeral=False)
        return

    thread = interaction.channel
    filename_hint = "image"
    if isinstance(thread, discord.Thread) and thread.parent_message_id:
        try:
            parent = await thread.parent.fetch_message(thread.parent_message_id)
            if parent.attachments:
                filename_hint = parent.attachments[0].filename.rsplit(".", 1)[0]
        except Exception:
            pass

    await _post_results(thread, png_bytes, ase_bytes, info, cfg, f"{filename_hint}.png")


# ---------------------------------------------------------------------------
# Slash commands (optional convenience)
# ---------------------------------------------------------------------------

@bot.tree.command(name="settings", description="Show current processing settings for this thread")
async def slash_settings(interaction: discord.Interaction):
    if not isinstance(interaction.channel, discord.Thread):
        await interaction.response.send_message("⚠️ This command only works inside an image thread.", ephemeral=True)
        return
    entry = await sessions.get_session(interaction.channel.id)
    if entry is None:
        await interaction.response.send_message("⚠️ No active session in this thread.", ephemeral=True)
        return
    cfg, _ = entry
    await interaction.response.send_message(f"**Current settings:**\n{_cfg_summary(cfg)}", ephemeral=True)


@bot.tree.command(name="removecolour", description="Add a hex colour to remove (e.g. #ffffff)")
async def slash_removecolour(interaction: discord.Interaction, colour: str, tolerance: int = 30):
    if not isinstance(interaction.channel, discord.Thread):
        await interaction.response.send_message("⚠️ This command only works inside an image thread.", ephemeral=True)
        return
    cfg = await sessions.add_remove_colour(interaction.channel.id, colour)
    if cfg is None:
        await interaction.response.send_message("⚠️ Invalid hex colour or no active session.", ephemeral=True)
        return
    await sessions.update_session(interaction.channel.id, colour_tolerance=tolerance)
    await interaction.response.send_message(
        f"✅ Added **{colour}** to removal list (tolerance: {tolerance})\n"
        f"Hit **▶ Process** to apply.", ephemeral=True
    )


@bot.tree.command(name="resize", description="Set output dimensions (0 = auto)")
async def slash_resize(interaction: discord.Interaction, width: int = 0, height: int = 0):
    if not isinstance(interaction.channel, discord.Thread):
        await interaction.response.send_message("⚠️ This command only works inside an image thread.", ephemeral=True)
        return
    await sessions.update_session(interaction.channel.id, output_width=width, output_height=height)
    await interaction.response.send_message(f"✅ Output size set to **{width}×{height}**", ephemeral=True)


@bot.tree.command(name="process", description="Re-process the image with current settings")
async def slash_process(interaction: discord.Interaction):
    if not isinstance(interaction.channel, discord.Thread):
        await interaction.response.send_message("⚠️ This command only works inside an image thread.", ephemeral=True)
        return
    await interaction.response.defer(thinking=True)
    await _reprocess(interaction, interaction.channel.id)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    if not TOKEN:
        raise RuntimeError("DISCORD_TOKEN environment variable is not set!")
    bot.run(TOKEN)