"""PUMA XML parser, including single-channel and multi-channel configurations."""

from __future__ import annotations
from pathlib import Path
from typing import Any
from src.core.convert import xml_to_dict
from src.preprocessing.parsers.parser import Parser


class PumaParser(Parser):
    def parse(self, file_path: Path) -> dict[str, Any]:
        """PUMA Sensitivity1.xml에서 채널별 광학·검사 Parameter를 추출한다.

        Args:
            self: 현재 클래스 인스턴스.
            file_path: 분석 대상 파일 경로.

        Returns:
            dict[str, Any]: 처리 결과를 반환한다."""
        data = xml_to_dict(file_path)
        root = data["RECIPE_COMPONENT_NEW"]["SENSITIVITY_COMPONENT"]
        polar_info = root["OPTICS"]["CARRERA_OPTICS"]["CARRERA_INSPECTION_OPTICS"]
        scan_line = self.leaf(polar_info.get("MAGNIFICATION"))
        incident = self.leaf(polar_info["CARRERA_INCIDENT_OPTICS"]["POLARIZATION"])
        if not incident:
            raise ValueError("PUMA incident polarization missing")
        polar_left = str(incident).split("-")[0].upper()
        params: dict[str, Any] = {}
        for ch in self.as_list(polar_info["CARRERA_COLLECTION_OPTICS"]):
            channel = str(self.leaf(ch.get("CHANNEL_NAME"), "")).split(" ")[0]
            right = str(self.leaf(ch.get("POLARIZATION"), ""))
            if not channel or not right:
                raise ValueError("PUMA collection channel/polarization missing")
            params.update(
                {
                    f"polarization_ch{channel}": f"{polar_left}/{right[0]}",
                    f"scan_line_ch{channel}": scan_line,
                }
            )
        ait = root["SENSITIVITY"]["ALGORITHM_DATA"]["ALGORITHM"][
            "DarkfieldDetectionParameters"
        ]["HLAT"]["HLATRecipe"]
        for ch in self.as_list(ait):
            channel = str(int(self.leaf(ch.get("channelID"), -1)) + 1)
            details = ch["REGION_PARAM_INFO"]
            keys = {
                "edge_factor": self.leaf(ch.get("EdgeDieFactor")),
                "filter": self.leaf(ch.get("PostFilter")),
                "threshold": self.leaf(details.get("Threshold")),
                "noise": self.leaf(details.get("Noise")),
                "absmin": self.leaf(details.get("MinDiff")),
                "gradient": self.leaf(details.get("Gradient")),
                "color": self.leaf(details.get("Color")),
            }
            params.update({f"{key}_ch{channel}": value for key, value in keys.items()})
        if not params:
            raise ValueError(f"No recognized PUMA parameters: {file_path}")
        return dict(sorted(params.items()))
