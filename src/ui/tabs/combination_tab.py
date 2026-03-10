"""Combination tab — Cartesian product management with checkboxes.

Displays all generated combinations and lets users enable/disable each.
"""

import logging

import customtkinter as ctk

from ...core.combination import CombinationGenerator, Combination
from ...tools.filter_tool import _fuzzy_resolve
from ...tools.base_tool import load_tool_config

log = logging.getLogger("claw")


class CombinationTab(ctk.CTkFrame):
    """Combination management tab with enable/disable checkboxes."""

    def __init__(self, parent):
        super().__init__(parent, fg_color="transparent")
        self._combo_gen: CombinationGenerator | None = None
        self._checkboxes: list[tuple[ctk.CTkCheckBox, ctk.IntVar]] = []
        self._filter_codes: dict = {}

        self._build_ui()

    def _build_ui(self) -> None:
        # Top controls
        top = ctk.CTkFrame(self, fg_color="transparent")
        top.pack(fill="x", padx=5, pady=5)

        ctk.CTkButton(
            top, text="生成组合", width=100, command=self._regenerate
        ).pack(side="left", padx=5)
        ctk.CTkButton(
            top, text="全选", width=70, command=self._select_all
        ).pack(side="left", padx=5)
        ctk.CTkButton(
            top, text="全不选", width=70, command=self._deselect_all
        ).pack(side="left", padx=5)

        self._count_label = ctk.CTkLabel(top, text="共 0 个组合", anchor="e")
        self._count_label.pack(side="right", padx=5)

        # Scrollable list
        self._scroll = ctk.CTkScrollableFrame(self)
        self._scroll.pack(fill="both", expand=True, padx=5, pady=5)

        self._empty_label = ctk.CTkLabel(
            self._scroll,
            text="请先在知识库中配置遍历参数，然后点击「生成组合」",
            text_color="gray",
        )
        self._empty_label.pack(pady=20)

    def load_traversal(self, traversal: dict) -> None:
        """Load traversal config and generate combinations."""
        self._combo_gen = CombinationGenerator(traversal)
        self._combo_gen.generate()

        # Load filter_codes for validation
        try:
            tool_config = load_tool_config()
            self._filter_codes = tool_config.get("filter_codes", {})
        except Exception:
            self._filter_codes = {}

        self._render_list()

    def _regenerate(self) -> None:
        """Re-generate from the current combo_gen (if loaded)."""
        if self._combo_gen:
            self._combo_gen.generate()
            self._render_list()

    def _render_list(self) -> None:
        """Render checkboxes for all combinations."""
        # Clear existing
        for widget in self._scroll.winfo_children():
            widget.destroy()
        self._checkboxes.clear()

        if not self._combo_gen:
            return

        combos = self._combo_gen.all_combinations
        if not combos:
            ctk.CTkLabel(
                self._scroll, text="无组合（遍历参数为空）", text_color="gray"
            ).pack(pady=20)
            self._count_label.configure(text="共 0 个组合")
            return

        for i, combo in enumerate(combos):
            # Check if any filter value is unresolvable
            label = combo.label
            if self._filter_codes:
                for dim, val in combo.filters.items():
                    if dim == "city":
                        continue
                    codes = self._filter_codes.get(dim, {})
                    if val and val != "不限" and _fuzzy_resolve(val, codes) is None:
                        label = f"[!] {label}"
                        break

            var = ctk.IntVar(value=1 if combo.enabled else 0)
            cb = ctk.CTkCheckBox(
                self._scroll,
                text=label,
                variable=var,
                command=lambda idx=i, v=var: self._on_toggle(idx, v),
            )
            cb.pack(anchor="w", padx=10, pady=2)
            self._checkboxes.append((cb, var))

        enabled = sum(1 for c in combos if c.enabled)
        self._count_label.configure(text=f"启用 {enabled}/{len(combos)} 个组合")

    def _on_toggle(self, index: int, var: ctk.IntVar) -> None:
        if self._combo_gen:
            self._combo_gen.set_enabled(index, bool(var.get()))
            combos = self._combo_gen.all_combinations
            enabled = sum(1 for c in combos if c.enabled)
            self._count_label.configure(
                text=f"启用 {enabled}/{len(combos)} 个组合"
            )

    def _select_all(self) -> None:
        if self._combo_gen:
            self._combo_gen.enable_all()
        for _, var in self._checkboxes:
            var.set(1)
        self._update_count()

    def _deselect_all(self) -> None:
        if self._combo_gen:
            self._combo_gen.disable_all()
        for _, var in self._checkboxes:
            var.set(0)
        self._update_count()

    def _update_count(self) -> None:
        if self._combo_gen:
            combos = self._combo_gen.all_combinations
            enabled = sum(1 for c in combos if c.enabled)
            self._count_label.configure(
                text=f"启用 {enabled}/{len(combos)} 个组合"
            )

    def get_enabled_labels(self) -> list[str]:
        """Return labels of enabled combinations."""
        if not self._combo_gen:
            return []
        return [c.label for c in self._combo_gen.get_enabled()]

    def get_enabled_combinations(self) -> list[Combination]:
        """Return enabled Combination objects."""
        if not self._combo_gen:
            return []
        return self._combo_gen.get_enabled()
