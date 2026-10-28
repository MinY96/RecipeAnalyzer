"""Parser contract and validation helpers."""

from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any


class Parser(ABC):
    name = "DM Recipe Parser"

    @abstractmethod
    def parse(self, file_path: Path) -> dict[str, Any]:
        """하위 Parser에서 구현할 Recipe 파일 파싱 인터페이스를 정의한다.

        Args:
            self: 현재 클래스 인스턴스.
            file_path: 분석 대상 파일 경로.

        Returns:
            dict[str, Any]: 처리 결과를 반환한다."""

    @staticmethod
    def as_list(value: Any) -> list:
        """단일 XML 노드와 반복 XML 노드를 동일한 리스트 형태로 변환한다.

        Args:
            value: 변환할 데이터 값.

        Returns:
            list: 처리 결과를 반환한다."""
        if value is None:
            return []
        return value if isinstance(value, list) else [value]

    @staticmethod
    def leaf(value: Any, default=None):
        """XML 딕셔너리 리프 노드에서 실제 값을 안전하게 추출한다.

        Args:
            value: 변환할 데이터 값.
            default: 조건에 맞는 값이 없을 때 사용할 기본값."""
        if not isinstance(value, dict):
            return default
        val = value.get("value")
        return val if val is not None else default

    @staticmethod
    def four(values: Any) -> list:
        """문자열로 기록된 채널별 수치 4개를 안전하게 추출한다.

        Args:
            values: 처리에 필요한 입력값.

        Returns:
            list: 처리 결과를 반환한다."""
        if isinstance(values, str):
            vals = values.split()
        elif isinstance(values, (list, tuple)):
            vals = list(values)
        else:
            vals = []
        return (vals + [None] * 4)[:4]
