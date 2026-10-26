"""컨버터 관련 함수"""

from pathlib import Path
import xml.etree.ElementTree as ET
from collections import defaultdict


def xml_to_dict(xml_source: str | Path, from_file: bool = True) -> dict:
    """XML 파일 또는 문자열을 딕셔너리로 변환하는 함수"""
    if from_file:
        root = ET.parse(xml_source).getroot()
    else:
        root = ET.fromstring(xml_source)

    def get_tag_name(tag: str) -> str:
        # Namespace URI 제거: {namespace}TAG -> TAG
        return tag.split("}", 1)[-1]

    def convert_value(value: str, dtype: str):
        if dtype.lower() in ("float", "double"):
            return float(value)
        if dtype.lower() in ("int", "integer"):
            return int(value)
        if dtype.lower() in ("bool", "boolean"):
            return value.strip().lower() in ("true", "1")
        return value

    def parse_element(element: ET.Element) -> dict:
        children = list(element)
        # 속성 분리
        dtype = None
        attributes = {}
        for key, value in element.attrib.items():
            attr_name = get_tag_name(key)
            if attr_name == "dt":
                dtype = value
            else:
                attributes[attr_name] = value
        # 1. Leaf 노드
        if not children:
            value = (element.text or "").strip()
            result = {
                "type": dtype or "string",
                "value": convert_value(value, dtype) if value and dtype else value,
            }
            if attributes:
                result["@attributes"] = attributes
            return result
        # 2. 하위 태그 그룹화
        grouped = defaultdict(list)
        for child in children:
            tag = get_tag_name(child.tag)
            grouped[tag].append(parse_element(child))
        result = {}
        if attributes:
            result["@attributes"] = attributes
        # 3. 동일 태그가 여러 개면 list
        for tag, items in grouped.items():
            result[tag] = items[0] if len(items) == 1 else items
        return result

    return {get_tag_name(root.tag): parse_element(root)}
