"""Evaluator Agent — the only LLM call point in V1.

Reads full JD, scores the job, generates personalized greeting
and pre-generates reply templates for follow-up scenarios.
"""

import json
import logging
from typing import Any

from .llm_client import LLMClient
from ..utils.paths import CONFIG_DIR


log = logging.getLogger("claw")

_PROMPT_DIR = CONFIG_DIR / "prompts"


class EvaluationResult:
    """Structured result from the Evaluator."""

    def __init__(self, data: dict):
        self.score: int = data.get("score", 0)
        self.decision: str = data.get("decision", "skip")
        self.reason: str = data.get("reason", "")
        self.greeting: str = data.get("greeting", "")
        self.reply_templates: dict = data.get("reply_templates", {})
        self._raw = data

    @property
    def is_match(self) -> bool:
        return self.decision == "match"

    def to_dict(self) -> dict:
        return self._raw


class Evaluator:
    """Evaluate a job posting against the user's profile.

    This is the ONLY LLM call point in V1. Every other operation
    is pure Python logic + Playwright automation.

    Usage:
        evaluator = Evaluator(llm_client, static_config)
        result = evaluator.evaluate(job_detail)
        if result.is_match:
            # Use result.greeting for chat initiation
            # Store result.reply_templates in DB
    """

    def __init__(self, llm_client: LLMClient, static_config: dict):
        self._llm = llm_client
        self._static = static_config
        self._system_prompt = self._load_system_prompt()

    def evaluate(self, job_detail: dict) -> EvaluationResult | None:
        """Evaluate a single job posting.

        Args:
            job_detail: Full job detail dict from JobDetailTool
                        (contains jobInfo, bossInfo, brandComInfo).

        Returns:
            EvaluationResult, or None if LLM call failed.
        """
        user_message = self._build_user_message(job_detail)

        job_name = job_detail.get("jobInfo", {}).get("jobName", "unknown")
        company = job_detail.get("brandComInfo", {}).get("brandName", "unknown")
        log.info(f"[LLM] 评估: {company} - {job_name}")

        try:
            result = self._llm.chat_json(
                system_prompt=self._system_prompt,
                user_message=user_message,
                temperature=0.3,
                max_tokens=800,
            )
        except ValueError as e:
            log.error(f"[LLM] JSON解析失败: {e}")
            return None
        except Exception as e:
            log.error(f"[LLM] API调用失败: {e}")
            return None

        # Validate required fields
        if "score" not in result or "decision" not in result:
            log.error(f"[LLM] 返回缺少必要字段: {list(result.keys())}")
            return None

        eval_result = EvaluationResult(result)
        log.info(
            f"[LLM] 结果: {eval_result.score}分 → {eval_result.decision}"
            f" | {eval_result.reason}"
        )
        return eval_result

    def _load_system_prompt(self) -> str:
        """Load system prompt template and fill in dynamic values."""
        path = _PROMPT_DIR / "evaluator_system.txt"
        template = path.read_text(encoding="utf-8")

        # Replace min_score placeholder
        scoring = self._static.get("scoring", {})
        min_score = scoring.get("min_score_to_apply", 60)
        return template.replace("{min_score}", str(min_score))

    def _build_user_message(self, job_detail: dict) -> str:
        """Assemble the user message (task packet) for the LLM.

        Combines: profile + profile_doc + personality + job detail.
        """
        profile = self._static.get("profile", {})
        personality = self._static.get("personality", {})
        scoring = self._static.get("scoring", {})

        job_info = job_detail.get("jobInfo", {})
        boss_info = job_detail.get("bossInfo", {})
        brand_info = job_detail.get("brandComInfo", {})

        parts = [
            "## 求职者画像",
            f"- 姓名: {profile.get('name', '未设置')}",
            f"- 技能: {profile.get('skills', '未设置')}",
            f"- 学历: {profile.get('education', '未设置')}",
            f"- 经验概述: {profile.get('experience_summary', '未设置')}",
            f"- 优势: {profile.get('strengths', '未设置')}",
        ]

        # Inject profile document if configured
        doc_content = self._read_profile_doc()
        if doc_content:
            parts.extend([
                "",
                "## 求职者详细资料（文档）",
                doc_content,
            ])

        parts.extend([
            "",
            "## 性格偏好",
            f"- 语气: {personality.get('tone', '专业')}",
            f"- 招呼风格: {personality.get('greeting_style', '')}",
            f"- 避免: {personality.get('avoid', '')}",
            "",
            "## 评分维度",
            f"- {scoring.get('dimensions', '匹配程度、薪资性价比')}",
            "",
            "## 职位信息",
            f"- 职位: {job_info.get('jobName', '')}",
            f"- 公司: {brand_info.get('brandName', '')}",
            f"- 行业: {brand_info.get('industryName', '')}",
            f"- 规模: {brand_info.get('scaleName', '')}",
            f"- 融资: {brand_info.get('stageName', '')}",
            f"- 薪资: {job_info.get('salaryDesc', '')}",
            f"- 经验要求: {job_info.get('experienceName', '')}",
            f"- 学历要求: {job_info.get('degreeName', '')}",
            f"- 工作地点: {job_info.get('address', '')}",
            f"- 技能标签: {', '.join(job_info.get('showSkills', []))}",
            f"- BOSS: {boss_info.get('name', '')} ({boss_info.get('title', '')})",
            f"- BOSS活跃: {boss_info.get('activeTimeDesc', '')}",
            "",
            "## 岗位描述",
            job_info.get("postDescription", "（无描述）"),
        ])

        return "\n".join(parts)

    def _read_profile_doc(self) -> str | None:
        """Read the user's profile document from config/profile.md.

        Returns:
            File content (truncated to ~3000 chars), or None.
        """
        path = CONFIG_DIR / "profile.md"

        if not path.is_file():
            return None

        try:
            content = path.read_text(encoding="utf-8").strip()
        except Exception as e:
            log.warning(f"[LLM] 读取个人资料文档失败: {e}")
            return None

        if not content:
            return None

        max_chars = 3000
        if len(content) > max_chars:
            log.info(
                f"[LLM] 个人资料文档已截断: {len(content)} → {max_chars} 字符"
            )
            content = content[:max_chars] + "\n…（已截断）"

        return content
