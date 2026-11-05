"""Matching Rate aggregate worker."""

from __future__ import annotations
import logging
from datetime import datetime
import numpy as np
import pandas as pd
from src.databases import PostgresCRUD

log = logging.getLogger(__name__)


def check_exclude_matching(df_orin: pd.DataFrame) -> pd.DataFrame:
    """집계 제외 Recipe 선정

    Args:
        df_orin (pd.DataFrame): Recipe 리스트

    Returns:
        pd.DataFrame: 수정된 Recipe 리스트
    """
    # STD_RECIPE = PostgresCRUD.select_query_by_list("select recipe from std_recipe",)
    test_device = PostgresCRUD.select_query_by_list(
        "select device from test_device",
    )
    results = []
    for row in df_orin.itertuples(index=False):
        (
            tech_prod,
            tech,
            prod_group,
            level,
            layer,
            mode,
            device,
            parse,
            model,
            master,
        ) = (
            row.tech_prod,
            row.tech,
            row.prod_group,
            row.level,
            row.layer,
            row.mode,
            row.device,
            row.parse_status,
            row.model,
            row.master_key,
        )
        # rcp_tag = layer[4:]
        if (device in test_device) or (prod_group == "TBD"):
            is_exc, code = True, "EXC_TEST"
        elif not isinstance(device, str) or len(device) != 4:
            is_exc, code = True, "EXC_DEVICE"
        elif not isinstance(layer, str) or len(layer) != 8:
            is_exc, code = True, "EXC_LAYER"
        # elif not (any(rcp.endswith(rcp_tag) for rcp in STD_RECIPE) or (layer in STD_RECIPE)):
        #     is_exc, code = True, "EXC_RECIPE"
        elif prod_group == "IGBT":
            is_exc, code = True, "EXC_IGBT"
        elif parse == "failed":
            is_exc, code = True, "EXC_PARSE"
        elif master is None:
            is_exc, code = True, "EXC_MASTER"
        elif model in ("KLA2139", "KLA2360", "KLA2365", "KLA2367") and not all(
            [tech_prod, tech, prod_group, level, layer, mode]
        ):
            is_exc, code = True, "EXC_MISS"
        elif model == "PUMA" and not all([tech_prod, tech, prod_group, level, layer]):
            is_exc, code = True, "EXC_MISS"
        else:
            is_exc, code = False, None
        results.append((is_exc, code))

    # 결과를 DataFrame에 바로 컬럼으로 할당 (merge 비용 절감)
    df_update = df_orin.copy()
    df_update["exclude_matching"] = [r[0] for r in results]
    df_update["exclude_code"] = [r[1] for r in results]
    return df_update


def check_para_matching(value: str, master: str) -> bool:
    """Master Value 비교 함수

    Args:
        value (str): Parsing Value
        master (str): Master Value

    Returns:
        bool: Matching 여부
    """
    if (master is None) | (value is None):
        return None
    if (isinstance(master, float)) and np.isnan(master):
        return None
    if "/" in master:
        return value in master.split("/")
    if "~" in master:
        lower, upper = map(float, master.split("~"))
        return lower <= float(value) <= upper
    if value is None:
        return False
    return str.upper(value) == str.upper(master)


def update_master_key() -> None:
    """Master 정보 저장 테이블의 기준정보와 수집정보 연결하여 master_key 업데이트"""
    query = """
    UPDATE dm_recipe_info AS dri
    SET master_key = dmri.master_key
    FROM dm_master_recipe_info AS dmri
    WHERE dri.model = dmri.model
        AND dri.tech_prod = dmri.tech_prod
        AND dri.tech = dmri.tech
        AND dri.prod_group = dmri.prod_group
        AND dri.level = dmri.level
        AND dri.layer = dmri.layer
        AND (
            dri.model = 'PUMA'
            OR dri.mode = dmri.mode
        )
    """
    PostgresCRUD.execute(query)


def load_recipe_parameter_value() -> pd.DataFrame:
    """Recipe의 Parameter별 Master Value 로드

    Returns:
        pd.DataFrame: Recipe/Parameter/MasterValue 정보
    """
    query = """
    SELECT 
        dri.model,
        dri.equip,
        dri.recipe_key,
        dri.master_key,
        dri.recipe, 
        drp.para_nm,
        drp.para_value AS value,
        dmrp.para_value AS master_value,
        drp.matching AS para_matching
    FROM dm_recipe_info AS dri
    LEFT JOIN dm_recipe_para AS drp
    ON (dri.recipe_key = drp.recipe_key)
    LEFT JOIN dm_master_recipe_para AS dmrp
    ON (
        dri.master_key = dmrp.master_key
            AND drp.para_nm = dmrp.para_nm
    )
    WHERE dri.is_remove = false
        AND dri.exclude_matching = false
        AND dri.master_key is not null
        AND drp.para_nm != 'mode'
        AND drp.para_value is not null
        AND dmrp.para_value is not null
    ORDER BY dri.recipe_key, drp.para_nm
    """
    return PostgresCRUD.select_query(
        query,
    )


def aggregate_para_matching(df_parameter: pd.DataFrame) -> pd.DataFrame:
    """Parameter 단위 Matching 집계 함수

    Args:
        df_parameter (pd.DataFrame): parameter 별 value/master 테이블

    Returns:
        pd.DataFrame: parameter 단위 Matching 집계 테이블
    """
    result = [
        check_para_matching(value, master)
        for value, master in zip(df_parameter["value"], df_parameter["master_value"])
    ]
    df_parameter["para_matching"] = result
    PostgresCRUD.upsert(
        "dm_recipe_para",
        "recipe_para_pk",
        df_parameter[["recipe_key", "para_nm", "para_matching"]].rename(
            columns={"para_matching": "matching"}
        ),
    )
    return df_parameter


def aggregate_recipe_matching(df_parameter: pd.DataFrame) -> pd.DataFrame:
    """Recipe 단위 Matching 집계 함수

    Args:
        df_parameter (pd.DataFrame): parameter 단위 Matching 테이블

    Returns:
        pd.DataFrame: Recipe 단위 Matching 집계 테이블
    """
    rcp_matching_info = {
        rcp_key: all(rcp_data["para_matching"])
        for rcp_key, rcp_data in df_parameter.groupby("recipe_key")
    }
    df_parameter["matching"] = df_parameter["recipe_key"].map(rcp_matching_info)
    PostgresCRUD.upsert(
        "dm_recipe_info",
        "dm_recipe_info_pkey",
        df_parameter[["recipe_key", "matching"]].drop_duplicates(
            subset=["recipe_key"], keep="first"
        ),
    )
    return df_parameter


def aggregate_matching_rate(
    df_recipe: pd.DataFrame, df_parameter: pd.DataFrame
) -> None:
    """장비 단위 Matching Rate 집계

    Args:
        df_recipe (pd.DataFrame): Recipe 정보 테이블
        df_parameter (pd.DataFrame): Parameter 정보 테이블
    """
    matching_result = []
    agg_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    for (equip, model), eq_data in df_recipe.groupby(["equip", "model"]):
        eq_matching_data = df_parameter[df_parameter["equip"] == equip].drop_duplicates(
            subset=["recipe_key"], keep="first"
        )
        matching_info = eq_matching_data["matching"].value_counts()
        agg_recipe = len(eq_matching_data)
        matching_recipe = int(matching_info.get(True, 0))
        matching_rate = matching_recipe / agg_recipe if agg_recipe > 0 else 0
        matching_result.append(
            {
                "model": model,
                "equip": equip,
                "recipe": len(eq_data),
                "matching": matching_recipe,
                "non_matching": int(matching_info.get(False, 0)),
                "exclude_matching": len(eq_data[eq_data["exclude_matching"]]),
                "matching_rate": matching_rate,
                "aggregation_time": agg_time,
            }
        )
    df_matching_rate = pd.DataFrame(matching_result)
    total_info = []
    for model, model_matching_data in df_matching_rate.groupby("model"):
        matching = int(model_matching_data["matching"].sum())
        non_matching = int(model_matching_data["non_matching"].sum())
        matching_rate = (
            matching / (matching + non_matching) if (matching + non_matching) > 0 else 0
        )
        total_info.append(
            {
                "model": model,
                "equip": "TOTAL",
                "recipe": int(model_matching_data["recipe"].sum()),
                "matching": matching,
                "non_matching": non_matching,
                "exclude_matching": int(model_matching_data["exclude_matching"].sum()),
                "matching_rate": matching_rate,
                "aggregation_time": agg_time,
            }
        )
    df_matching_rate = pd.concat(
        [df_matching_rate, pd.DataFrame(total_info)], ignore_index=True
    )
    PostgresCRUD.upsert("dm_matching_rate", "dm_matching_rate_pkey", df_matching_rate)


class MatchingAggregator:
    @staticmethod
    def run() -> None:
        """전체 장비 Recipe의 Matching Rate를 집계한다."""
        log.info(
            "Matching Aggreagtor started",
        )
        # 1. Master Key 업데이트
        update_master_key()

        # 2. dm_recipe 불러오기
        df_recipe = PostgresCRUD.select_query(
            "SELECT * FROM dm_recipe_info WHERE is_remove=false",
        )

        # 3. 집계 제외 Recipe 업데이트
        df_recipe = check_exclude_matching(df_recipe)
        PostgresCRUD.upsert(
            "dm_recipe_info",
            "dm_recipe_info_pkey",
            df_recipe[["recipe_key", "exclude_matching", "exclude_code"]],
        )

        # 4. Recipe의 Parameter별 Master Value 불러오기
        df_para = load_recipe_parameter_value()

        # 5. dm_recipe_para Matching 집계 및 업데이트 - Parameter 단위 Matching
        df_para = aggregate_para_matching(df_para)

        # 6. dm_recipe_info Matching 집계 및 업데이트 - Recipe 단위 Matching
        df_para = aggregate_recipe_matching(df_para)

        # 7. 장비 단위 Matching Rate 집계 및 업데이트
        aggregate_matching_rate(df_recipe, df_para)
        log.info(
            "Matching Aggreagtor finished",
        )
