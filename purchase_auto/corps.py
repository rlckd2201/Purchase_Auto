from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class CorpConfig:
    code: str
    display_name: str
    legal_name: str
    business_numbers: tuple[str, ...]
    approval_form_label: str
    finance_reference_group: str
    aliases: tuple[str, ...]


CORPS: dict[str, CorpConfig] = {
    "daeseung": CorpConfig(
        code="daeseung",
        display_name="대승",
        legal_name="(주)대승",
        business_numbers=("125-81-05619", "403-85-07607", "403-85-23311"),
        approval_form_label="대승 - (관리총괄)기안용지(관리직)",
        finance_reference_group="재정_대승",
        aliases=("대승", "D1", "D2", "D3", "DS", "(주)대승", "주식회사 대승"),
    ),
    "daeseung_precision": CorpConfig(
        code="daeseung_precision",
        display_name="대승정밀",
        legal_name="대승정밀(주)",
        business_numbers=("125-81-32697", "403-85-15640", "844-85-00770", "118-85-07029"),
        approval_form_label="대승정밀 - (관리총괄)기안용지(관리직)",
        finance_reference_group="재정_대승정밀",
        aliases=("대승정밀", "P1", "P2", "P3", "P4", "DSJM", "대승정밀(주)", "주식회사 대승정밀"),
    ),
    "ilgang": CorpConfig(
        code="ilgang",
        display_name="일강",
        legal_name="(주)일강",
        business_numbers=("125-81-51622", "403-85-20895"),
        approval_form_label="일강 - (경영)기안용지",
        finance_reference_group="재정_일강",
        aliases=("일강", "IG", "(주)일강", "주식회사 일강"),
    ),
}


def normalize_corp(value: str) -> str:
    text = (value or "").strip().lower().replace(" ", "")
    for code, corp in CORPS.items():
        candidates = (code, corp.display_name, corp.legal_name, *corp.aliases)
        for candidate in candidates:
            if text == candidate.strip().lower().replace(" ", ""):
                return code
    raise ValueError(f"지원하지 않는 법인입니다: {value}")


def get_corp(value: str) -> CorpConfig:
    return CORPS[normalize_corp(value)]
