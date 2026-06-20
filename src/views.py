"""
Discord UI components — button panels, modals, selects.
All views are persistent-capable (custom_id based) for robustness.
"""

from __future__ import annotations

import discord
from discord import ui


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def _cfg_summary(cfg) -> str:
    parts = []
    parts.append(f"📐 Output: {'auto' if not cfg.output_width else f'{cfg.output_width}×{cfg.output_height}'}")
    parts.append(f"🎨 Palette: {cfg.max_colours} colours")
    parts.append(f"🔲 Pixel snap: {'on' if cfg.pixel_snap else 'off'}")
    parts.append(f"🗑️ Remove BG: {'on' if cfg.remove_bg else 'off'}")
    if cfg.remove_colours:
        hexes = [f"#{r:02x}{g:02x}{b:02x}" for r, g, b in cfg.remove_colours]
        parts.append(f"❌ Removed colours: {', '.join(hexes)}")
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Modal: Remove specific colour
# ---------------------------------------------------------------------------

class RemoveColourModal(ui.Modal, title="Remove a Colour"):
    hex_input = ui.TextInput(
        label="Hex colour (e.g. #ffffff)",
        placeholder="#ffffff",
        min_length=4,
        max_length=7,
        required=True,
    )
    tolerance_input = ui.TextInput(
        label="Tolerance (0–255, default 30)",
        placeholder="30",
        required=False,
        max_length=3,
    )

    def __init__(self, callback_fn):
        super().__init__()
        self._cb = callback_fn

    async def on_submit(self, interaction: discord.Interaction):
        hex_val = self.hex_input.value.strip()
        tol_raw = self.tolerance_input.value.strip()
        tol = int(tol_raw) if tol_raw.isdigit() else 30
        tol = max(0, min(255, tol))
        await self._cb(interaction, hex_val, tol)


# ---------------------------------------------------------------------------
# Modal: Resize output
# ---------------------------------------------------------------------------

class ResizeModal(ui.Modal, title="Set Output Size"):
    width_input = ui.TextInput(
        label="Width in pixels (0 = auto)",
        placeholder="64",
        required=False,
        max_length=4,
    )
    height_input = ui.TextInput(
        label="Height in pixels (0 = auto)",
        placeholder="64",
        required=False,
        max_length=4,
    )

    def __init__(self, callback_fn):
        super().__init__()
        self._cb = callback_fn

    async def on_submit(self, interaction: discord.Interaction):
        w = int(self.width_input.value) if self.width_input.value.strip().isdigit() else 0
        h = int(self.height_input.value) if self.height_input.value.strip().isdigit() else 0
        await self._cb(interaction, w, h)


# ---------------------------------------------------------------------------
# Modal: BG tolerance
# ---------------------------------------------------------------------------

class BGToleranceModal(ui.Modal, title="Background Removal Settings"):
    tolerance_input = ui.TextInput(
        label="BG Tolerance (0–255, default 80)",
        placeholder="80",
        required=False,
        max_length=3,
    )
    alpha_input = ui.TextInput(
        label="Alpha threshold (0–255, default 12)",
        placeholder="12",
        required=False,
        max_length=3,
    )

    def __init__(self, callback_fn):
        super().__init__()
        self._cb = callback_fn

    async def on_submit(self, interaction: discord.Interaction):
        tol_raw = self.tolerance_input.value.strip()
        alp_raw = self.alpha_input.value.strip()
        tol = max(0, min(255, int(tol_raw) if tol_raw.isdigit() else 80))
        alp = max(0, min(255, int(alp_raw) if alp_raw.isdigit() else 12))
        await self._cb(interaction, tol, alp)


# ---------------------------------------------------------------------------
# Palette select
# ---------------------------------------------------------------------------

class PaletteSelect(ui.Select):
    def __init__(self, callback_fn):
        self._cb = callback_fn
        options = [
            discord.SelectOption(label="Unlimited (256)", value="256", description="Keep all colours"),
            discord.SelectOption(label="128 colours", value="128"),
            discord.SelectOption(label="64 colours", value="64"),
            discord.SelectOption(label="32 colours", value="32"),
            discord.SelectOption(label="16 colours", value="16"),
            discord.SelectOption(label="8 colours", value="8"),
            discord.SelectOption(label="4 colours", value="4"),
        ]
        super().__init__(placeholder="Choose max palette size…", options=options, custom_id="palette_select")

    async def callback(self, interaction: discord.Interaction):
        await self._cb(interaction, int(self.values[0]))


# ---------------------------------------------------------------------------
# Resampling select
# ---------------------------------------------------------------------------

class ResampleSelect(ui.Select):
    def __init__(self, callback_fn):
        self._cb = callback_fn
        options = [
            discord.SelectOption(label="Nearest (pixel perfect)", value="nearest", description="Best for pixel art"),
            discord.SelectOption(label="Bilinear (smooth)", value="bilinear"),
            discord.SelectOption(label="Lanczos (sharp)", value="lanczos"),
        ]
        super().__init__(placeholder="Resampling mode…", options=options, custom_id="resample_select")

    async def callback(self, interaction: discord.Interaction):
        await self._cb(interaction, self.values[0])


# ---------------------------------------------------------------------------
# Main control panel view
# ---------------------------------------------------------------------------

class ControlPanel(ui.View):
    """
    The main button panel posted in every image thread.
    Buttons are organised into rows (max 5 per row, max 5 rows).
    """

    def __init__(self, process_fn, thread_id: int):
        super().__init__(timeout=None)  # persistent
        self._process = process_fn
        self._thread_id = thread_id

    # ── Row 0: primary actions ──────────────────────────────────────────────

    @ui.button(label="▶ Process", style=discord.ButtonStyle.success, row=0, custom_id="btn_process")
    async def btn_process(self, interaction: discord.Interaction, button: ui.Button):
        await interaction.response.defer(thinking=True)
        await self._process(interaction, self._thread_id)

    @ui.button(label="↩ Reset", style=discord.ButtonStyle.secondary, row=0, custom_id="btn_reset")
    async def btn_reset(self, interaction: discord.Interaction, button: ui.Button):
        await interaction.response.defer(ephemeral=True)
        import sessions
        cfg = await sessions.reset_session(self._thread_id)
        if cfg:
            await interaction.followup.send(
                f"✅ Settings reset to defaults.\n{_cfg_summary(cfg)}", ephemeral=True
            )

    @ui.button(label="📋 Settings", style=discord.ButtonStyle.primary, row=0, custom_id="btn_settings")
    async def btn_settings(self, interaction: discord.Interaction, button: ui.Button):
        import sessions
        entry = await sessions.get_session(self._thread_id)
        if entry is None:
            await interaction.response.send_message("⚠️ Session not found.", ephemeral=True)
            return
        cfg, _, _fn = entry
        await interaction.response.send_message(
            f"**Current settings:**\n{_cfg_summary(cfg)}", ephemeral=True
        )

    # ── Row 1: background ───────────────────────────────────────────────────

    @ui.button(label="🗑️ Toggle Remove BG", style=discord.ButtonStyle.secondary, row=1, custom_id="btn_togglebg")
    async def btn_togglebg(self, interaction: discord.Interaction, button: ui.Button):
        import sessions
        entry = await sessions.get_session(self._thread_id)
        if entry is None:
            await interaction.response.send_message("⚠️ Session not found.", ephemeral=True)
            return
        cfg, _, _fn = entry
        new_val = not cfg.remove_bg
        await sessions.update_session(self._thread_id, remove_bg=new_val)
        state = "ON ✅" if new_val else "OFF ❌"
        await interaction.response.send_message(f"Background removal: **{state}**", ephemeral=True)

    @ui.button(label="⚙️ BG Tolerance", style=discord.ButtonStyle.secondary, row=1, custom_id="btn_bgtol")
    async def btn_bgtol(self, interaction: discord.Interaction, button: ui.Button):
        async def _cb(inter, tol, alp):
            import sessions
            await sessions.update_session(self._thread_id, bg_tolerance=tol, transparency_threshold=alp)
            await inter.response.send_message(
                f"✅ BG tolerance set to **{tol}**, alpha threshold to **{alp}**", ephemeral=True
            )
        await interaction.response.send_modal(BGToleranceModal(_cb))

    # ── Row 2: colours ──────────────────────────────────────────────────────

    @ui.button(label="❌ Remove Colour", style=discord.ButtonStyle.danger, row=2, custom_id="btn_rmcolour")
    async def btn_rmcolour(self, interaction: discord.Interaction, button: ui.Button):
        async def _cb(inter, hex_val, tol):
            import sessions
            cfg = await sessions.add_remove_colour(self._thread_id, hex_val)
            if cfg is None:
                await inter.response.send_message("⚠️ Invalid hex or session not found.", ephemeral=True)
                return
            await sessions.update_session(self._thread_id, colour_tolerance=tol)
            await inter.response.send_message(
                f"✅ Will remove colour **{hex_val}** (tolerance {tol})\n"
                f"Total colours to remove: {len(cfg.remove_colours)}",
                ephemeral=True,
            )
        await interaction.response.send_modal(RemoveColourModal(_cb))

    @ui.button(label="🧹 Clear Removed Colours", style=discord.ButtonStyle.secondary, row=2, custom_id="btn_clrcolours")
    async def btn_clrcolours(self, interaction: discord.Interaction, button: ui.Button):
        import sessions
        cfg = await sessions.clear_remove_colours(self._thread_id)
        if cfg:
            await interaction.response.send_message("✅ Cleared all colour removals.", ephemeral=True)

    @ui.button(label="🎨 Palette Size", style=discord.ButtonStyle.secondary, row=2, custom_id="btn_palette")
    async def btn_palette(self, interaction: discord.Interaction, button: ui.Button):
        async def _cb(inter, value: int):
            import sessions
            await sessions.update_session(self._thread_id, max_colours=value)
            await inter.response.send_message(f"✅ Max palette set to **{value}** colours", ephemeral=True)
        view = ui.View(timeout=60)
        view.add_item(PaletteSelect(_cb))
        await interaction.response.send_message("Choose palette size:", view=view, ephemeral=True)

    # ── Row 3: sizing & pixel grid ──────────────────────────────────────────

    @ui.button(label="📐 Resize", style=discord.ButtonStyle.secondary, row=3, custom_id="btn_resize")
    async def btn_resize(self, interaction: discord.Interaction, button: ui.Button):
        async def _cb(inter, w, h):
            import sessions
            await sessions.update_session(self._thread_id, output_width=w, output_height=h)
            label = f"{w}×{h}" if w and h else ("auto width" if not w else f"width {w}")
            await inter.response.send_message(f"✅ Output size set to **{label}**", ephemeral=True)
        await interaction.response.send_modal(ResizeModal(_cb))

    @ui.button(label="🔲 Toggle Pixel Snap", style=discord.ButtonStyle.secondary, row=3, custom_id="btn_pixsnap")
    async def btn_pixsnap(self, interaction: discord.Interaction, button: ui.Button):
        import sessions
        entry = await sessions.get_session(self._thread_id)
        if entry is None:
            await interaction.response.send_message("⚠️ Session not found.", ephemeral=True)
            return
        cfg, _, _fn = entry
        new_val = not cfg.pixel_snap
        await sessions.update_session(self._thread_id, pixel_snap=new_val)
        state = "ON ✅" if new_val else "OFF ❌"
        await interaction.response.send_message(f"Pixel snap: **{state}**", ephemeral=True)

    @ui.button(label="🔍 Resample Mode", style=discord.ButtonStyle.secondary, row=3, custom_id="btn_resample")
    async def btn_resample(self, interaction: discord.Interaction, button: ui.Button):
        async def _cb(inter, mode: str):
            import sessions
            await sessions.update_session(self._thread_id, resampling=mode)
            await inter.response.send_message(f"✅ Resampling set to **{mode}**", ephemeral=True)
        view = ui.View(timeout=60)
        view.add_item(ResampleSelect(_cb))
        await interaction.response.send_message("Choose resampling:", view=view, ephemeral=True)

    # ── Row 4: trim ─────────────────────────────────────────────────────────

    @ui.button(label="✂️ Toggle Trim Edges", style=discord.ButtonStyle.secondary, row=4, custom_id="btn_trim")
    async def btn_trim(self, interaction: discord.Interaction, button: ui.Button):
        import sessions
        entry = await sessions.get_session(self._thread_id)
        if entry is None:
            await interaction.response.send_message("⚠️ Session not found.", ephemeral=True)
            return
        cfg, _, _fn = entry
        new_val = not cfg.trim_edges
        await sessions.update_session(self._thread_id, trim_edges=new_val)
        state = "ON ✅" if new_val else "OFF ❌"
        await interaction.response.send_message(f"Trim transparent edges: **{state}**", ephemeral=True)