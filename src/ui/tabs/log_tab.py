"""Log tab — real-time log viewer.

Displays log entries with color-coded severity levels.
Auto-scrolls to bottom. Supports clearing.
"""

import logging

import customtkinter as ctk


# Level → color mapping
_LEVEL_COLORS = {
    logging.DEBUG: "#888888",
    logging.INFO: "#CCCCCC",
    logging.WARNING: "#FFD700",
    logging.ERROR: "#FF6B6B",
    logging.CRITICAL: "#FF3333",
}


class LogTab(ctk.CTkFrame):
    """Real-time log display tab."""

    def __init__(self, parent):
        super().__init__(parent, fg_color="transparent")
        self._max_lines = 2000
        self._line_count = 0
        self._build_ui()

    def _build_ui(self) -> None:
        # Top controls
        top = ctk.CTkFrame(self, fg_color="transparent")
        top.pack(fill="x", padx=5, pady=5)

        ctk.CTkButton(
            top, text="清空日志", width=80, command=self.clear
        ).pack(side="right", padx=5)

        self._auto_scroll_var = ctk.IntVar(value=1)
        ctk.CTkCheckBox(
            top, text="自动滚动", variable=self._auto_scroll_var,
        ).pack(side="right", padx=10)

        self._line_label = ctk.CTkLabel(top, text="0 行", anchor="w")
        self._line_label.pack(side="left", padx=5)

        # Log text area
        self._textbox = ctk.CTkTextbox(
            self,
            font=ctk.CTkFont(family="Consolas", size=12),
            state="disabled",
            wrap="word",
        )
        self._textbox.pack(fill="both", expand=True, padx=5, pady=5)

        # Configure text tags for color
        # customtkinter CTkTextbox uses internal _textbox
        inner = self._textbox._textbox
        for level, color in _LEVEL_COLORS.items():
            inner.tag_configure(f"level_{level}", foreground=color)

    def append(self, text: str, level: int = logging.INFO) -> None:
        """Append a log line with color based on level."""
        self._textbox.configure(state="normal")

        tag = f"level_{level}"
        inner = self._textbox._textbox

        # Trim if too many lines
        if self._line_count >= self._max_lines:
            inner.delete("1.0", "2.0")
            self._line_count -= 1

        inner.insert("end", text + "\n", tag)
        self._line_count += 1

        self._textbox.configure(state="disabled")

        # Auto-scroll
        if self._auto_scroll_var.get():
            self._textbox.see("end")

        self._line_label.configure(text=f"{self._line_count} 行")

    def clear(self) -> None:
        """Clear all log entries."""
        self._textbox.configure(state="normal")
        self._textbox._textbox.delete("1.0", "end")
        self._textbox.configure(state="disabled")
        self._line_count = 0
        self._line_label.configure(text="0 行")
