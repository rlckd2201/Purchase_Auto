# 사업자번호-공장 매핑 전수 확인

## 확인 대상
- WMS 화면 사업자 선택 목록
- WMS backend `FACTORY_BY_BUSINESS_NUMBER`
- Purchase_Auto 그룹웨어 품의 공장명 산정
- Purchase_Auto 컴퓨존 주문서 사업자 선택

## 기준 매핑
| 법인 | 공장 | 사업자번호 |
|---|---|---|
| 대승 | D1공장 | 125-81-05619 |
| 대승 | D2공장 | 403-85-07607 |
| 대승 | D3공장 | 403-85-23311 |
| 대승정밀 | P1공장 | 125-81-32697 |
| 대승정밀 | P2공장 | 118-85-07029 |
| 대승정밀 | P3공장 | 403-85-15640 |
| 대승정밀 | P4공장 | 844-85-00770 |
| 일강 | 일강1공장 | 125-81-51622 |
| 일강 | 일강2공장 | 403-85-20895 |

## 조치
- Purchase_Auto 테스트에 9개 사업장 전체 매핑 검증을 추가했다.
- 그룹웨어 품의 본문 공장명은 사업자번호 기준으로 위 표와 일치해야 한다.
- 컴퓨존 주문서 사업자 선택은 D/P 공장명 기준으로 위 표와 일치해야 한다.
- 일강은 컴퓨존 주문 사업자 선택 시 메모의 선택 사업자번호를 그대로 사용한다.

## 검증
- `py_compile tests/test_state_flow.py purchase_auto/compuzone_order.py purchase_auto/groupware_approval.py`
- `pytest tests/test_state_flow.py -q`: 56 passed
