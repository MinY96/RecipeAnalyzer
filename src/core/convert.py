"""XML to dictionary, preserving legacy leaf {'type', 'value'} representation."""

from pathlib import Path
import xml.etree.ElementTree as ET
from collections import defaultdict


def xml_to_dict(xml_source: str | Path, from_file: bool = True) -> dict:
    """XML 파일 또는 문자열을 중첩 딕셔너리로 재귀 변환한다.

    Args:
        xml_source: 변환할 XML 파일 또는 XML 문자열.
        from_file: 파일에서 XML을 읽을지 여부.

    Returns:
        dict: 처리 결과를 반환한다."""
    root = ET.parse(xml_source).getroot() if from_file else ET.fromstring(xml_source)

    def get_tag_name(tag: str) -> str:
        """XML 네임스페이스를 제거하고 실제 태그 이름을 반환한다.

        Args:
            tag: XML 태그.

        Returns:
            str: 처리 결과를 반환한다."""
        return tag.split("}", 1)[-1]

    def convert_value(value: str, dtype: str):
        """XML 노드 값에 기록된 데이터 타입에 따라 문자열을 변환한다.

        Args:
            value: 변환할 데이터 값.
            dtype: XML 타입 이름."""
        if dtype.lower() in ("float", "double"):
            return float(value)
        if dtype.lower() in ("int", "integer"):
            return int(value)
        if dtype.lower() in ("bool", "boolean"):
            return value.strip().lower() in ("true", "1")
        return value

    def parse_element(element: ET.Element) -> dict:
        """XML 노드의 속성·값·하위 노드를 변환하고 동일 태그를 그룹화한다.

        Args:
            element: XML 노드 객체.

        Returns:
            dict: 처리 결과를 반환한다."""
        children = list(element)
        dtype = None
        attributes = {}
        for key, value in element.attrib.items():
            attr_name = get_tag_name(key)
            if attr_name == "dt":
                dtype = value
            else:
                attributes[attr_name] = value
        if not children:
            value = (element.text or "").strip()
            result = {
                "type": dtype or "string",
                "value": convert_value(value, dtype) if value and dtype else value,
            }
            if attributes:
                result["@attributes"] = attributes
            return result
        grouped = defaultdict(list)
        for child in children:
            grouped[get_tag_name(child.tag)].append(parse_element(child))
        result = {}
        if attributes:
            result["@attributes"] = attributes
        for tag, items in grouped.items():
            result[tag] = items[0] if len(items) == 1 else items
        return result

    return {get_tag_name(root.tag): parse_element(root)}
