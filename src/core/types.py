"""Shared types."""

from typing import Any

Record = dict[str, Any]


class SafeList(list):
    """Allow positive out-of-bounds indexing without IndexError."""

    def __getitem__(self, index):
        """리스트 인덱스가 범위를 초과할 때 예외 대신 None을 반환한다.

        Args:
            self: 현재 클래스 인스턴스.
            index: 조회할 배열 인덱스."""
        if isinstance(index, int) and index >= len(self):
            return None
        return super().__getitem__(index)
