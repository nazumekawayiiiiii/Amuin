"""Combination generator — Cartesian product with tag-based filtering.

Generates all combinations from traversal config dimensions,
then allows user to enable/disable specific combinations via tags.
"""

import logging
from itertools import product

log = logging.getLogger("claw")


class Combination:
    """A single search combination (keyword + filters)."""

    def __init__(self, keyword: str, filters: dict[str, str], enabled: bool = True):
        self.keyword = keyword
        self.filters = filters
        self.enabled = enabled

    @property
    def label(self) -> str:
        parts = [self.keyword]
        for dim in ("city", "salary", "experience", "degree", "industry", "scale"):
            val = self.filters.get(dim)
            if val:
                parts.append(val)
        return " + ".join(parts)

    def to_dict(self) -> dict:
        return {
            "keyword": self.keyword,
            "filters": self.filters,
            "enabled": self.enabled,
            "label": self.label,
        }


class CombinationGenerator:
    """Generates Cartesian product of traversal parameters.

    Given:
        keywords: ["Python开发", "后端开发"]
        cities: ["深圳", "广州"]
        salary: ["10-20K"]

    Produces:
        Python开发 + 深圳 + 10-20K
        Python开发 + 广州 + 10-20K
        后端开发 + 深圳 + 10-20K
        后端开发 + 广州 + 10-20K

    Empty dimensions are skipped (not included in product).

    Usage:
        gen = CombinationGenerator(traversal_config)
        combos = gen.generate()
        enabled = gen.get_enabled()
    """

    def __init__(self, traversal: dict):
        self._traversal = traversal
        self._combinations: list[Combination] = []

    def generate(self) -> list[Combination]:
        """Generate all combinations via Cartesian product."""
        keywords = self._traversal.get("keywords", [])
        if not keywords:
            return []

        # Build dimension lists (only non-empty dimensions)
        filter_dims: list[tuple[str, list[str]]] = []
        for dim in ("city", "salary", "experience", "degree", "industry", "scale"):
            # Map traversal key to filter dimension name
            traversal_key = "cities" if dim == "city" else dim
            values = self._traversal.get(traversal_key, [])
            if values:
                filter_dims.append((dim, values))

        if not filter_dims:
            # No filters, just keywords
            self._combinations = [
                Combination(kw, {}) for kw in keywords
            ]
        else:
            dim_names = [d[0] for d in filter_dims]
            dim_values = [d[1] for d in filter_dims]

            self._combinations = []
            for kw in keywords:
                for combo_values in product(*dim_values):
                    filters = dict(zip(dim_names, combo_values))
                    self._combinations.append(Combination(kw, filters))

        log.info(
            f"[INIT] 笛卡尔积: {len(self._combinations)} 个组合 "
            f"(关键词 {len(keywords)} × 筛选维度 {len(filter_dims)})"
        )
        return self._combinations

    def get_enabled(self) -> list[Combination]:
        """Get only enabled combinations."""
        return [c for c in self._combinations if c.enabled]

    def set_enabled(self, index: int, enabled: bool) -> None:
        """Enable or disable a specific combination by index."""
        if 0 <= index < len(self._combinations):
            self._combinations[index].enabled = enabled

    def enable_all(self) -> None:
        for c in self._combinations:
            c.enabled = True

    def disable_all(self) -> None:
        for c in self._combinations:
            c.enabled = False

    @property
    def all_combinations(self) -> list[Combination]:
        return self._combinations
