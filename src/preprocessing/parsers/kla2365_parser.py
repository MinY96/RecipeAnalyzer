"""KLA2365 XML parameter extraction; explicit error when required hierarchy is absent."""

from __future__ import annotations
from pathlib import Path
from typing import Any
from src.core.convert import xml_to_dict
from src.preprocessing.parsers.parser import Parser


class KLA2365Parser(Parser):
    def parse(self, file_path: Path) -> dict[str, Any]:
        """KLA2365 Sensitivity1.xml에서 광학·Threshold Parameter를 추출한다.

        Args:
            self: 현재 클래스 인스턴스.
            file_path: 분석 대상 파일 경로.

        Returns:
            dict[str, Any]: 처리 결과를 반환한다."""
        data = xml_to_dict(file_path)
        root = data["RECIPE_COMPONENT_NEW"]["SENSITIVITY_COMPONENT"]
        photon = root["OPTICS"]["STEALTH_OPTICS"]["PHOTON_OPTICS"]
        sensitivity = root["SENSITIVITY"]
        params = {
            "pixel": self.leaf(photon.get("PIXEL_SIZE")),
            "focus_offset": self.leaf(photon.get("OBJECTFOCUS_OFFSET")),
            "filter": None,
            "size_sieve": self.leaf(sensitivity.get("SIZE_SIEVE")),
            "mode": None,
            "threshold": None,
        }
        filter_setting = self.leaf(sensitivity.get("FILTER_SETTINGS_NAME"))
        params["filter"] = (
            str(filter_setting).strip() or None if filter_setting is not None else None
        )
        sat = sensitivity["ALGORITHM_DATA"]["brightFieldLeafAlgorithmParameters"][
            "region"
        ]["algorithm"]["sat"]
        break_points = self.leaf(sat["frontEnd"].get("meanBreakPoints"), "")
        mode = "AUTO" if not str(break_points).strip() else "SAT"
        params["mode"] = mode
        primary = sat["threshold"]["primaryDie"]
        if mode == "AUTO":
            params["threshold"] = self.leaf(primary.get("offsetThresholds"))
        else:
            mappings = (
                ("maxThresholds", "th_max_s"),
                ("offsetThresholds", "th_offset_s"),
                ("targetDensities", "density_target_s"),
            )
            for key, prefix in mappings:
                for index, value in enumerate(self.four(self.leaf(primary.get(key)))):
                    params[f"{prefix}{index}"] = value
        for prefix in ("th_max_s", "th_offset_s", "density_target_s"):
            for index in range(4):
                params.setdefault(f"{prefix}{index}", None)
        return dict(sorted(params.items()))
