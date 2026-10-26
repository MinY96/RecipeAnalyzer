# pylint: disable=W0718
"""KLA365 Recipe Parser"""

from typing import Dict, Any
from pathlib import Path


class Parser:
    """Recipe Parser"""

    def __init__(self):
        self.name = "DM Recipe Parser"

    def parse(self, file_path: Path) -> Dict[str, Any]:
        return {}
