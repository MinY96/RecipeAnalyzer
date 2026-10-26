# pylint: disable=W0718
"""KLA365 Recipe Parser"""

from pathlib import Path
from typing import Dict, Any

from src.core.convert import xml_to_dict
from src.preprocessing.parsers.parser import Parser


class KLA2365Parser(Parser):
    """KLA2365 Recipe Parser"""

    def parse(self, file_path: Path) -> Dict[str, Any]:
        """KLA2365 Recipe 파일 파싱 메소드

        Args:
            file_path (Path): Recipe 파일 경로

        Returns:
            Dict[str, Any]: 파싱 결과
        """
        params = {
            "pixel": None,
            "focus_offset": None,
            "filter": None,
            "size_sieve": None,
            "mode": None,
            "threshold": None,
            "th_max_s0": None,
            "th_max_s1": None,
            "th_max_s2": None,
            "th_max_s3": None,
            "th_offset_s0": None,
            "th_offset_s1": None,
            "th_offset_s2": None,
            "th_offset_s3": None,
            "density_target_s0": None,
            "density_target_s1": None,
            "density_target_s2": None,
            "density_target_s3": None,
        }
        try:
            data = xml_to_dict(file_path)
            orin = data["RECIPE_COMPONENT_NEW"]["SENSITIVITY_COMPONENT"]
            photon_optics = orin["OPTICS"]["STEALTH_OPTICS"]["PHOTON_OPTICS"]
            sensitivity = orin["SENSITIVITY"]

            # 1. Pixel / Focus Offset
            pixel = photon_optics.get("PIXEL_SIZE", {"value": None}).get("value")
            focus_offset = photon_optics.get("OBJECTFOCUS_OFFSET", {"value": None}).get(
                "value"
            )
            params.update({"pixel": pixel, "focus_offset": focus_offset})

            # 2. Filter / Size Sieve
            filter_setting = (
                sensitivity.get("FILTER_SETTINGS_NAME", {"value": None})
                .get("value")
                .strip()
            )
            if not filter_setting:
                filter_setting = None
            size_sieve = sensitivity.get("SIZE_SIEVE", {"value": None}).get("value")
            params.update({"filter": filter_setting, "size_sieve": size_sieve})

            # 3. Mode
            sat = sensitivity["ALGORITHM_DATA"]["brightFieldLeafAlgorithmParameters"][
                "region"
            ]["algorithm"]["sat"]
            mode = (
                sat["frontEnd"]
                .get("meanBreakPoints", {"value": ""})
                .get("value")
                .strip()
            )
            mode = "AUTO" if not mode else "SAT"
            params.update({"mode": mode})

            # 4. Treshold / SAT
            primary_die = sat["threshold"]["primaryDie"]
            if mode == "AUTO":
                threshold = primary_die.get("offsetThresholds", {"value": None}).get(
                    "value"
                )
                params.update({"threshold": threshold})
            else:
                th_max = (
                    primary_die.get("maxThresholds", {"value": "   "})
                    .get("value")
                    .split(" ")[:4]
                )
                params.update(
                    {
                        "th_max_s0": th_max[0],
                        "th_max_s1": th_max[1],
                        "th_max_s2": th_max[2],
                        "th_max_s3": th_max[3],
                    }
                )
                th_offset = (
                    primary_die.get("offsetThresholds", {"value": "   "})
                    .get("value")
                    .split(" ")[:4]
                )
                params.update(
                    {
                        "th_offset_s0": th_offset[0],
                        "th_offset_s1": th_offset[1],
                        "th_offset_s2": th_offset[2],
                        "th_offset_s3": th_offset[3],
                    }
                )
                density_target = (
                    primary_die.get("targetDensities", {"value": "   "})
                    .get("value")
                    .split(" ")[:4]
                )
                params.update(
                    {
                        "density_target_s0": density_target[0],
                        "density_target_s1": density_target[1],
                        "density_target_s2": density_target[2],
                        "density_target_s3": density_target[3],
                    }
                )
            params = dict(sorted(params.items(), key=lambda x: x[0]))
        except Exception as e:
            print(e)
        return params
