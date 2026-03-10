"""Knowledge tab — edit knowledge.json via GUI.

Sections: LLM config, traversal parameters, profile, personality, preferences, limits.
"""

import json
import logging
from pathlib import Path

import customtkinter as ctk

log = logging.getLogger("claw")


class KnowledgeTab(ctk.CTkFrame):
    """Knowledge base editor tab."""

    def __init__(self, parent, config_path: Path):
        super().__init__(parent, fg_color="transparent")
        self._config_path = config_path
        self._knowledge: dict = {}
        self._entries: dict[str, ctk.CTkEntry | ctk.CTkTextbox] = {}
        self.on_save: callable = None  # Callback after save

        self._load_file()
        self._build_ui()

    def _load_file(self) -> None:
        try:
            with open(self._config_path, "r", encoding="utf-8") as f:
                self._knowledge = json.load(f)
        except Exception as e:
            log.warning(f"[INIT] 知识库加载失败: {e}")
            self._knowledge = {}

    def _build_ui(self) -> None:
        # Scrollable container
        scroll = ctk.CTkScrollableFrame(self)
        scroll.pack(fill="both", expand=True, padx=5, pady=5)

        # ── LLM 配置 ──
        self._add_section(scroll, "LLM 配置")
        llm = self._knowledge.get("llm", {})
        self._add_field(scroll, "base_url", "API 地址", llm.get("base_url", ""))
        self._add_field(scroll, "api_key", "API Key", llm.get("api_key", ""), show="*")
        self._add_field(scroll, "model", "模型名称", llm.get("model", ""))

        # ── 筛选遍历参数 ──
        self._add_section(scroll, "筛选遍历参数 (逗号分隔，需与 BOSS直聘筛选项一致)")
        traversal = self._knowledge.get("traversal", {})
        for key, label in [
            ("keywords", "关键词"),
            ("cities", "城市"),
            ("salary", "薪资"),
            ("experience", "经验"),
            ("degree", "学历"),
            ("industry", "行业"),
            ("scale", "规模"),
        ]:
            values = traversal.get(key, [])
            self._add_field(
                scroll, f"traversal.{key}", label,
                ", ".join(values) if values else ""
            )

        # ── 个人画像 ──
        self._add_section(scroll, "个人画像")
        profile = self._knowledge.get("static", {}).get("profile", {})
        for key, label in [
            ("name", "姓名"),
            ("skills", "技能"),
            ("education", "学历"),
            ("experience_summary", "经验概述"),
            ("strengths", "优势"),
        ]:
            self._add_field(scroll, f"profile.{key}", label, profile.get(key, ""))

        # Profile document status
        doc_path = self._config_path.parent / "profile.md"
        if doc_path.is_file():
            size = doc_path.stat().st_size
            doc_text = f"已检测到个人文档 ({size / 1024:.1f} KB)"
        else:
            doc_text = "未配置个人文档 — 将简历内容放入 config/profile.md 即可"
        ctk.CTkLabel(
            scroll, text=doc_text, text_color="#999999",
            font=ctk.CTkFont(size=12),
        ).pack(anchor="w", padx=10, pady=(2, 5))

        # ── 性格偏好 ──
        self._add_section(scroll, "性格偏好")
        personality = self._knowledge.get("static", {}).get("personality", {})
        for key, label in [
            ("tone", "语气风格"),
            ("greeting_style", "跟进语言风格"),
            ("avoid", "避免的表达"),
        ]:
            self._add_field(
                scroll, f"personality.{key}", label, personality.get(key, "")
            )

        # ── 偏好设置 ──
        self._add_section(scroll, "偏好设置")
        prefs = self._knowledge.get("static", {}).get("preferences", {})
        self._add_field(
            scroll, "pref.company_blacklist", "公司黑名单 (逗号分隔)",
            ", ".join(prefs.get("company_blacklist", []))
        )
        self._add_field(
            scroll, "pref.keyword_blacklist", "关键词黑名单 (逗号分隔)",
            ", ".join(prefs.get("keyword_blacklist", []))
        )
        self._add_field(
            scroll, "pref.overtime_attitude", "加班态度",
            prefs.get("overtime_attitude", "")
        )

        # ── 限制 ──
        self._add_section(scroll, "自动化限制")
        limits = self._knowledge.get("static", {}).get("limits", {})
        self._add_field(
            scroll, "limits.daily_apply_max", "每日投递上限",
            str(limits.get("daily_apply_max", 40))
        )
        self._add_field(
            scroll, "limits.min_score", "最低评分 (0-99分)",
            str(self._knowledge.get("static", {}).get("scoring", {}).get(
                "min_score_to_apply", 60
            ))
        )
        rest = limits.get("rest_duration_sec", [120, 300])
        self._add_field(
            scroll, "limits.rest_min", "组合间休息最短 (秒)",
            str(rest[0] if rest else 120)
        )
        self._add_field(
            scroll, "limits.rest_max", "组合间休息最长 (秒)",
            str(rest[1] if len(rest) > 1 else 300)
        )

        # ── Bottom buttons ──
        btn_frame = ctk.CTkFrame(self, fg_color="transparent")
        btn_frame.pack(fill="x", padx=5, pady=5)
        ctk.CTkButton(
            btn_frame, text="保存配置", command=self.save
        ).pack(side="right", padx=5)
        ctk.CTkButton(
            btn_frame, text="清空全部", width=80,
            command=self._clear_all,
            fg_color="#666666", hover_color="#555555",
        ).pack(side="right", padx=5)

        self._warning_label = ctk.CTkLabel(
            btn_frame, text="", text_color="#FFD700", anchor="w",
            wraplength=700, font=ctk.CTkFont(size=12),
        )
        self._warning_label.pack(side="left", padx=5)

    def _add_section(self, parent, title: str) -> None:
        ctk.CTkLabel(
            parent, text=title,
            font=ctk.CTkFont(size=15, weight="bold"),
        ).pack(anchor="w", padx=5, pady=(15, 5))

    def _add_field(
        self, parent, key: str, label: str, value: str, show: str = ""
    ) -> None:
        row = ctk.CTkFrame(parent, fg_color="transparent")
        row.pack(fill="x", padx=5, pady=2)

        ctk.CTkLabel(row, text=label, width=140, anchor="w").pack(side="left")
        entry = ctk.CTkEntry(row, show=show if show else None)
        entry.pack(side="left", fill="x", expand=True, padx=(5, 0))
        if value:
            entry.insert(0, value)
        self._entries[key] = entry

    def _get_entry(self, key: str) -> str:
        entry = self._entries.get(key)
        return entry.get().strip() if entry else ""

    def _get_list(self, key: str) -> list[str]:
        text = self._get_entry(key)
        if not text:
            return []
        return [s.strip() for s in text.split(",") if s.strip()]

    def get_knowledge(self) -> dict:
        """Read current form values into a knowledge dict."""
        return {
            "llm": {
                "base_url": self._get_entry("base_url"),
                "api_key": self._get_entry("api_key"),
                "model": self._get_entry("model"),
            },
            "traversal": {
                "keywords": self._get_list("traversal.keywords"),
                "cities": self._get_list("traversal.cities"),
                "salary": self._get_list("traversal.salary"),
                "experience": self._get_list("traversal.experience"),
                "degree": self._get_list("traversal.degree"),
                "industry": self._get_list("traversal.industry"),
                "scale": self._get_list("traversal.scale"),
            },
            "static": {
                "profile": {
                    "name": self._get_entry("profile.name"),
                    "skills": self._get_entry("profile.skills"),
                    "education": self._get_entry("profile.education"),
                    "experience_summary": self._get_entry("profile.experience_summary"),
                    "strengths": self._get_entry("profile.strengths"),
                },
                "personality": {
                    "tone": self._get_entry("personality.tone"),
                    "greeting_style": self._get_entry("personality.greeting_style"),
                    "avoid": self._get_entry("personality.avoid"),
                },
                "preferences": {
                    "remote_ok": False,
                    "overtime_attitude": self._get_entry("pref.overtime_attitude"),
                    "company_blacklist": self._get_list("pref.company_blacklist"),
                    "keyword_blacklist": self._get_list("pref.keyword_blacklist"),
                },
                "limits": {
                    "daily_apply_max": int(
                        self._get_entry("limits.daily_apply_max") or "40"
                    ),
                    "operation_interval_sec": self._knowledge.get(
                        "static", {}
                    ).get("limits", {}).get("operation_interval_sec", [5, 15]),
                    "rest_every_n_operations": self._knowledge.get(
                        "static", {}
                    ).get("limits", {}).get("rest_every_n_operations", 10),
                    "rest_duration_sec": [
                        int(self._get_entry("limits.rest_min") or "120"),
                        int(self._get_entry("limits.rest_max") or "300"),
                    ],
                },
                "scoring": {
                    "min_score_to_apply": int(
                        self._get_entry("limits.min_score") or "60"
                    ),
                    "dimensions": self._knowledge.get("static", {}).get(
                        "scoring", {}
                    ).get("dimensions", "匹配程度、薪资性价比"),
                },
            },
        }

    def _clear_all(self) -> None:
        """Clear all form fields."""
        for entry in self._entries.values():
            entry.delete(0, "end")

    def save(self) -> None:
        """Save current form values to knowledge.json."""
        knowledge = self.get_knowledge()
        try:
            with open(self._config_path, "w", encoding="utf-8") as f:
                json.dump(knowledge, f, ensure_ascii=False, indent=2)
            log.info("[INIT] 知识库已保存")
            if self.on_save:
                self.on_save()
        except Exception as e:
            log.error(f"[ERROR] 知识库保存失败: {e}")
            return

        # Validate filter values
        from ...tools.filter_tool import FilterTool
        from ...tools.base_tool import load_tool_config

        tool_config = load_tool_config()
        filter_codes = tool_config.get("filter_codes", {})
        warnings = FilterTool.validate_filter_values(
            knowledge.get("traversal", {}), filter_codes
        )
        if warnings:
            self._warning_label.configure(text=" | ".join(warnings[:3]))
        else:
            self._warning_label.configure(text="")
