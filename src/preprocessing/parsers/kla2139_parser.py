"""KLA2139 ins.dat parser; raises on malformed input rather than swallowing errors."""

from __future__ import annotations
from pathlib import Path
from typing import Any
from src.core.types import SafeList
from src.preprocessing.parsers.parser import Parser


class KLA2139Parser(Parser):
    def parse(self, file_path: Path) -> dict[str, Any]:
        """KLA2139 ins.dat의 Optics·Threshold·Focus Parameter를 추출한다.

        Args:
            self: 현재 클래스 인스턴스.
            file_path: 분석 대상 파일 경로.

        Returns:
            dict[str, Any]: 처리 결과를 반환한다."""
        data = None
        # Preserve legacy fallback sequence; latin1 decodes any bytes and is last.
        for encoding in ("utf-8", "cp949", "euc-kr", "latin1"):
            try:
                with file_path.open("r", encoding=encoding) as stream:
                    data = [line.rstrip("\r\n").split("\t") for line in stream]
                break
            except UnicodeDecodeError:
                continue
        if data is None or not data:
            raise ValueError(f"Unparseable or empty KLA2139 file: {file_path}")
        info = self._analyze_recipe_lines(data)
        if not info:
            raise ValueError(f"KLA2139 test[0]. structure missing: {file_path}")

        pixel = self._safe_get_first(info, [".lens_desc.", ".pixel_size"])
        try:
            pixel = format(float(pixel), ".2f").rstrip("0").rstrip(".")
        except (ValueError, TypeError):
            pixel = None
        focus = self._safe_get_first(
            info, [".premap_sites.", ".site[0].", ".focus_offset"]
        )
        threshold = self._safe_get_first(info, [".sens.", ".ds_thresha"])
        thresh_mode = self._safe_get_first(info, [".sens.", ".thresh_mode"])
        mode = {"a": "AUTO", "s": "SAT", "f": "FIXED"}.get(thresh_mode)
        filter_value = self._safe_get_first(
            info, [".sens.", ".filter.", ".fdata.", ".name"]
        )
        filter_value = filter_value.upper() if filter_value is not None else None
        th_seg = SafeList(
            self._numeric_values(
                self._safe_get(info, [".sens.", ".sat_convolver_bounds"], [])
            )
        )
        th_max = SafeList(
            self._numeric_values(
                self._safe_get(info, [".sens.", ".sat_threshold_max"], [])
            )
        )
        th_offset = SafeList(
            self._numeric_values(
                self._safe_get(info, [".sens.", ".sat_threshold_offset"], [])
            )
        )
        edge = {"0": "Exclude", "5": "Include"}.get(
            self._safe_get_first(info, [".splan.", ".edge_cells"])
        )
        result = {
            "pixel": pixel,
            "mode": mode,
            "filter": filter_value,
            "threshold": threshold,
            "edge": edge,
            "focus_offset": focus,
        }
        for i in range(4):
            result[f"th_max_s{i}"] = th_max[i]
            result[f"th_offset_s{i}"] = th_offset[i]
            result[f"th_offset_r{i}"] = th_seg[i]
        if all(v is None for v in result.values()):
            raise ValueError(f"No recognized KLA2139 parameters: {file_path}")
        return result

    @staticmethod
    def _numeric_values(values: Any) -> list[int]:
        """텍스트로 기록된 설정값들을 숫자로 변환하여 반환한다.

        Args:
            values: 처리에 필요한 입력값.

        Returns:
            list[int]: 처리 결과를 반환한다."""
        if not isinstance(values, list):
            return []
        return [
            int(v) if v is not None and str(v).strip() else None for v in values[:4]
        ]

    @staticmethod
    def _safe_get(d: dict, keys: list[str], default=None):
        """중첩 딕셔너리의 키 경로를 따라 값을 안전하게 조회한다.

        Args:
            d: 처리에 필요한 입력값.
            keys: Recipe 또는 조건의 키 목록.
            default: 조건에 맞는 값이 없을 때 사용할 기본값."""
        for key in keys:
            if isinstance(d, dict) and key in d:
                d = d[key]
            else:
                return default
        return d

    def _safe_get_first(self, d: dict, keys: list[str]):
        """중첩 키 조회 결과가 리스트라면 첫 원소를 반환한다.

        Args:
            self: 현재 클래스 인스턴스.
            d: 처리에 필요한 입력값.
            keys: Recipe 또는 조건의 키 목록."""
        value = self._safe_get(d, keys, [None])
        return value[0] if isinstance(value, list) and value else None

    @staticmethod
    def _check_line_level(line: list[str]) -> int:
        """탭 분리된 KLA2139 설정행의 계층 레벨을 계산한다.

        Args:
            line: FTP 또는 Recipe 텍스트 한 행.

        Returns:
            int: 처리 결과를 반환한다."""
        count = 0
        for token in line:
            if token:
                break
            count += 1
        return count

    def _analyze_recipe_lines(self, lines: list[list[str]]) -> dict:
        """KLA2139 설정행을 키별 중첩 자료구조로 변환한다.

        Args:
            self: 현재 클래스 인스턴스.
            lines: 처리에 필요한 입력값.

        Returns:
            dict: 처리 결과를 반환한다."""
        root: dict = {}
        path: list[str | None] = [None] * 5
        for line in lines:
            level = self._check_line_level(line)
            if level >= 5 or len(line) <= level:
                continue
            key = line[level]
            path[level] = key
            for depth in range(level + 1, 5):
                path[depth] = None
            if level == 0:
                root[key] = {}
                continue
            if path[0] is None:
                raise ValueError("KLA2139 hierarchy missing root")
            current = root[path[0]]
            for depth in range(1, level):
                parent = path[depth]
                if parent is None:
                    raise ValueError("KLA2139 hierarchy missing parent")
                if not isinstance(current.get(parent), dict):
                    current[parent] = {}
                current = current[parent]
            current[key] = line[level + 1 :]
        return root.get("test[0].", {})
