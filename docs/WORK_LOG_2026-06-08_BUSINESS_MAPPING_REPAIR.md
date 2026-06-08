# 사업자번호-공장 매핑 재점검 및 복구

작성일: 2026-06-08

## 증상

- WMS 장바구니 사업장 선택에서 `P4공장 / 844-85-00770`으로 표시됐다.
- 사용자가 P4 사업자번호가 잘못됐다고 지적했다.

## 원인

- 2026-05-27 커밋 `b69b249 Fix P4 tax business selection`에서는 P공장 매핑이 아래처럼 잡혀 있었다.
  - P1: `125-81-32697`
  - P2: `403-85-15640`
  - P3: `844-85-00770`
  - P4: `118-85-07029`
- 이후 2026-05-29 커밋 `dd8826c Audit business factory mappings`에서 테스트/작업 로그 기준표를 만들며 P2/P3/P4 번호가 잘못 재정렬됐다.
- 그 잘못된 기준표가 WMS 화면 선택 목록, WMS backend 매핑, Purchase_Auto 그룹웨어/컴퓨존 매핑 기준으로 다시 퍼졌다.

## 복구 기준표

| 법인 | 공장 | 사업자번호 |
|---|---|---|
| 대승 | D1공장 | `125-81-05619` |
| 대승 | D2공장 | `403-85-07607` |
| 대승 | D3공장 | `403-85-23311` |
| 대승정밀 | P1공장 | `125-81-32697` |
| 대승정밀 | P2공장 | `403-85-15640` |
| 대승정밀 | P3공장 | `844-85-00770` |
| 대승정밀 | P4공장 | `118-85-07029` |
| 일강 | 일강1공장 | `125-81-51622` |
| 일강 | 일강2공장 | `403-85-20895` |

## 수정 대상

- WMS frontend `PURCHASE_COMPANIES`
- WMS frontend `PURCHASE_DELIVERIES.gimje-it.defaultBusinessNumber`
- WMS backend `FACTORY_BY_BUSINESS_NUMBER`
- Purchase_Auto `purchase_auto/compuzone_order.py::FACTORY_BUSINESS_NUMBERS`
- Purchase_Auto `purchase_auto/groupware_approval.py::FACTORY_BY_BUSINESS_NUMBER`
- Purchase_Auto `tests/test_state_flow.py::BUSINESS_FACTORY_CASES`
- 잘못된 매핑이 들어간 작업 로그/기술명세서

## 검증 기준

- WMS 화면 사업장 선택:
  - P1공장 / `125-81-32697`
  - P2공장 / `403-85-15640`
  - P3공장 / `844-85-00770`
  - P4공장 / `118-85-07029`
- 김제 전산팀 기본 배송지:
  - `P3공장`
  - 기본 사업자번호 `844-85-00770`
- P4공장 구매:
  - 품의 제목 `P4공장`
  - 컴퓨존 사업자 선택 `118-85-07029`
  - 그룹웨어 본문 공장명 `P4공장`
