"""Common type aliases."""

from typing import Any

Record = dict[str, Any]


class SafeList(list):
    """인덱싱 오류 제거된 리스트"""

    def __init__(self, init_list):
        super().__init__(init_list)

    def __getitem__(self, index):
        if index >= len(self):
            return None
        return super().__getitem__(index)
