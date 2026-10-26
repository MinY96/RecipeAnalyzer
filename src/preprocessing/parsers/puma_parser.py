# pylint: disable=W0718
"""PUMA Recipe Parser"""

from pathlib import Path
from typing import Dict, Any

from src.core.convert import xml_to_dict
from src.preprocessing.parsers.parser import Parser


class PumaParser(Parser):
    """PUMA Recipe Parser"""

    def parse(self, file_path: Path) -> Dict[str, Any]:
        """PUMA Recipe 파일 파싱 메소드

        Args:
            file_path (Path): Recipe 파일 경로

        Returns:
            Dict[str, Any]: 파싱 결과
        """
        params = {}
        try:
            data = xml_to_dict(file_path)
            # 1. Scan Line / Polarization
            polar_info = data["RECIPE_COMPONENT_NEW"]["SENSITIVITY_COMPONENT"][
                "OPTICS"
            ]["CARRERA_OPTICS"]["CARRERA_INSPECTION_OPTICS"]

            scan_line = polar_info.get("MAGNIFICATION", {"value": None}).get("value")

            polar_left = (
                polar_info["CARRERA_INCIDENT_OPTICS"]["POLARIZATION"]["value"]
                .split("-")[0]
                .upper()
            )
            for ch_dict in polar_info["CARRERA_COLLECTION_OPTICS"]:
                ch_name = (
                    ch_dict.get("CHANNEL_NAME", {"value": ""})
                    .get("value", None)
                    .split(" ")[0]
                )
                polar_right = ch_dict.get("POLARIZATION", {"value": ""}).get(
                    "value", ""
                )[0]
                polarization = f"{polar_left}/{polar_right}"
                params.update(
                    {
                        f"polarization_ch{ch_name}": polarization,
                        f"scan_line_ch{ch_name}": scan_line,
                    }
                )

            # 2. AIT Parameter
            ait_info = data["RECIPE_COMPONENT_NEW"]["SENSITIVITY_COMPONENT"][
                "SENSITIVITY"
            ]["ALGORITHM_DATA"]["ALGORITHM"]["DarkfieldDetectionParameters"]["HLAT"][
                "HLATRecipe"
            ]
            for ch_dict in ait_info:
                ch_name = str(
                    int(ch_dict.get("channelID", {"value": "-1"}).get("value", -1)) + 1
                )
                edge_factor = ch_dict.get("EdgeDieFactor", {"value": None}).get("value")
                p_filter = ch_dict.get("PostFilter", {"value": None}).get("value")
                details = ch_dict["REGION_PARAM_INFO"]
                threshold = details.get("Threshold", {"value": None}).get("value")
                noise = details.get("Noise", {"value": None}).get("value")
                absmin = details.get("MinDiff", {"value": None}).get("value")
                gradient = details.get("Gradient", {"value": None}).get("value")
                color = details.get("Color", {"value": None}).get("value")
                params.update(
                    {
                        f"edge_factor_ch{ch_name}": edge_factor,
                        f"filter_ch{ch_name}": p_filter,
                        f"threshold_ch{ch_name}": threshold,
                        f"noise_ch{ch_name}": noise,
                        f"absmin_ch{ch_name}": absmin,
                        f"gradient_ch{ch_name}": gradient,
                        f"color_ch{ch_name}": color,
                    }
                )
            params = dict(sorted(params.items(), key=lambda x: x[0]))
        except Exception as e:
            print(e)
        return params
