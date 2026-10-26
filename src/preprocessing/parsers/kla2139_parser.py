# pylint: disable=W0718
"""KLA2139 Recipe Parser"""

from pathlib import Path
from typing import Dict, List, Any

from src.core.types import SafeList
from src.preprocessing.parsers.parser import Parser


class KLA2139Parser(Parser):
    """KLA2139 Recipe Parser"""

    def parse(self, file_path: Path) -> Dict[str, Any]:
        """KLA2139 Recipe 파일 파싱 메소드

        Args:
            file_path (Path): Recipe 파일 경로

        Returns:
            Dict[str, Any]: 파싱 결과
        """
        params = {
            "pixel": None,
            "mode": None,
            "filter": None,
            "threshold": None,
            "edge": None,
            "focus_offset": None,
            "th_max_s0": None,
            "th_max_s1": None,
            "th_max_s2": None,
            "th_max_s3": None,
            "th_offset_s0": None,
            "th_offset_s1": None,
            "th_offset_s2": None,
            "th_offset_s3": None,
            "th_offset_r0": None,
            "th_offset_r1": None,
            "th_offset_r2": None,
            "th_offset_r3": None,
        }
        lines = None
        encodings = ["utf-8", "cp949", "latin1", "euc-kr"]
        for enc in encodings:
            try:
                with open(file_path, "r", encoding=enc) as file:
                    lines = file.readlines()
                break  # 성공하면 반복문 탈출
            except UnicodeDecodeError:
                continue  # 인코딩 에러 시 다음 인코딩 시도
            except Exception as e:
                print(f"{file_path.name}: {e}")
                break

        if lines is not None:
            lines = [info.rstrip().split("\t") for info in lines]
        else:
            print(f"{file_path.name}: 모든 인코딩 시도 실패")
            lines = []
        try:
            key_info = self._analyze_recipe_lines(lines)
            pixel = self._safe_get_first(key_info, [".lens_desc.", ".pixel_size"])
            try:
                pixel = format(float(pixel), ".2f").rstrip("0").rstrip(".")
            except Exception:
                pixel = None
            focus = self._safe_get_first(
                key_info, [".premap_sites.", ".site[0].", ".focus_offset"]
            )
            threshold = self._safe_get_first(key_info, [".sens.", ".ds_thresha"])
            thresh_modes = {"a": "AUTO", "s": "SAT", "f": "FIXED"}
            temp = self._safe_get_first(key_info, [".sens.", ".thresh_mode"])
            mode = thresh_modes.get(temp, None)
            filter_para = self._safe_get_first(
                key_info, [".sens.", ".filter.", ".fdata.", ".name"]
            )
            filter_para = str.upper(filter_para) if filter_para is not None else None
            th_seg = SafeList(
                list(
                    map(
                        int,
                        self._safe_get(
                            key_info, [".sens.", ".sat_convolver_bounds"], []
                        )[:4],
                    )
                )
            )
            th_max = SafeList(
                list(
                    map(
                        int,
                        self._safe_get(key_info, [".sens.", ".sat_threshold_max"], [])[
                            :4
                        ],
                    )
                )
            )
            th_offset = SafeList(
                list(
                    map(
                        int,
                        self._safe_get(
                            key_info, [".sens.", ".sat_threshold_offset"], []
                        )[:4],
                    )
                )
            )
            edge_cells = {"0": "Exclude", "5": "Include"}
            temp = self._safe_get_first(key_info, [".splan.", ".edge_cells"])
            edge = edge_cells.get(temp, None)
            params = {
                "pixel": pixel,
                "mode": mode,
                "filter": filter_para,
                "threshold": threshold,
                "edge": edge,
                "focus_offset": focus,
                "th_max_s0": th_max[0],
                "th_max_s1": th_max[1],
                "th_max_s2": th_max[2],
                "th_max_s3": th_max[3],
                "th_offset_s0": th_offset[0],
                "th_offset_s1": th_offset[1],
                "th_offset_s2": th_offset[2],
                "th_offset_s3": th_offset[3],
                "th_offset_r0": th_seg[0],
                "th_offset_r1": th_seg[1],
                "th_offset_r2": th_seg[2],
                "th_offset_r3": th_seg[3],
            }
        except Exception as e:
            print(e)
        return params

    def _safe_get(self, d, keys, default=None):
        for key in keys:
            if isinstance(d, dict) and key in d:
                d = d[key]
            else:
                return default
        return d

    def _safe_get_first(self, d, keys):
        val = self._safe_get(d, keys, default=[None])
        if isinstance(val, list) and len(val) > 0:
            return val[0]
        return None

    def _analyze_recipe_lines(self, lines: List[str]) -> Dict[int, Any]:
        """Recipe 파일(리스트 형태) 분석 메소드

        Args:
            lines (List[str]): 줄 단위 읽은 Recipe 파일 정보

        Returns:
            Dict[int, any]: 라인 별 분석 결과
        """
        first_key = None
        second_key = None
        third_key = None
        fourth_key = None
        fifth_key = None
        key_info = {}
        for line in lines:
            level = self._check_line_level(line)
            if len(line) > level:
                key = line[level]
                try:
                    value = line[level + 1 :]
                except Exception:
                    value = None
                if level == 0:
                    first_key = key
                    key_info[first_key] = {}
                elif level == 1:
                    second_key = key
                    key_info[first_key][second_key] = value
                elif level == 2:
                    third_key = key
                    if not isinstance(key_info[first_key][second_key], dict):
                        key_info[first_key][second_key] = {}
                    key_info[first_key][second_key][third_key] = value
                elif level == 3:
                    fourth_key = key
                    if not isinstance(key_info[first_key][second_key][third_key], dict):
                        key_info[first_key][second_key][third_key] = {}
                    key_info[first_key][second_key][third_key][fourth_key] = value
                elif level == 4:
                    fifth_key = key
                    if not isinstance(
                        key_info[first_key][second_key][third_key][fourth_key], dict
                    ):
                        key_info[first_key][second_key][third_key][fourth_key] = {}
                    key_info[first_key][second_key][third_key][fourth_key][
                        fifth_key
                    ] = value
        return key_info.get("test[0].", {})

    def _check_line_level(self, line: str) -> int:
        """Recipe 한 줄마다 레벨 체크

        Args:
            line (str): Recipe 파일의 특정 한 줄

        Returns:
            int: 레벨
        """
        cnt = 0
        for check in [item == "" for item in line]:
            if check:
                cnt += 1
            else:
                break
        return cnt
