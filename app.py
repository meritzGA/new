"""
지원매니저용 사용인 자연어 조회 앱
- 매니저 코드 로그인
- 자연어 프롬프트 → Claude API → pandas 필터로 변환
- 본인 산하 사용인만 집계 조회
"""

import streamlit as st
import pandas as pd
import requests
import json
import re
import io
from datetime import datetime

# Anthropic import with error handling
try:
    from anthropic import Anthropic
    ANTHROPIC_AVAILABLE = True
except ImportError as e:
    st.error(f"Anthropic 라이브러리 임포트 실패: {e}")
    st.error("requirements.txt 파일에 anthropic==0.96.0이 포함되어 있는지 확인하세요.")
    st.stop()
    ANTHROPIC_AVAILABLE = False

# ─────────────────────────────────────────────
# 설정
# ─────────────────────────────────────────────
st.set_page_config(page_title="사용인 조회", page_icon="🔍", layout="wide")

# GitHub에 올릴 데이터 파일의 raw URL (secrets.toml 에서 관리)
# 예: https://raw.githubusercontent.com/<user>/<repo>/main/data/prizebase_202604.xlsx
def _safe_secret(key: str, default: str = "") -> str:
    try:
        return st.secrets.get(key, default)
    except Exception:
        return default

DATA_URL = _safe_secret("DATA_URL", "")
ANTHROPIC_API_KEY = _safe_secret("ANTHROPIC_API_KEY", "")

# ─────────────────────────────────────────────
# 유틸: 엑셀 _x0033_ 같은 이스케이프 문자열 복원
# ─────────────────────────────────────────────
_ESCAPE_RE = re.compile(r"_x([0-9A-Fa-f]{4})_")

def _unescape(val):
    if isinstance(val, str) and "_x" in val:
        return _ESCAPE_RE.sub(lambda m: chr(int(m.group(1), 16)), val)
    return val

def clean_dataframe(df: pd.DataFrame) -> pd.DataFrame:
    # 문자열 컬럼에 대해서만 언이스케이프 적용
    obj_cols = df.select_dtypes(include=["object"]).columns
    for c in obj_cols:
        df[c] = df[c].map(_unescape)
    df.columns = [_unescape(c) for c in df.columns]
    return df

# ─────────────────────────────────────────────
# 데이터 로딩 (GitHub raw URL에서)
# ─────────────────────────────────────────────
@st.cache_data(ttl=3600, show_spinner="데이터를 불러오는 중...")
def load_data(url: str) -> pd.DataFrame:
    if not url:
        raise ValueError("DATA_URL이 설정되지 않았습니다. .streamlit/secrets.toml 에 DATA_URL 을 추가하세요.")
    
    try:
        r = requests.get(url, timeout=60)
        r.raise_for_status()
        
        # GitHub에서 HTML 페이지를 받았는지 확인
        content_type = r.headers.get('content-type', '').lower()
        if 'text/html' in content_type:
            raise ValueError(f"GitHub URL이 HTML 페이지를 반환했습니다. URL을 확인하세요: {url}")
        
        # Excel 파일 읽기 (엔진 명시)
        df = pd.read_excel(io.BytesIO(r.content), engine='openpyxl')
        
        if df.empty:
            raise ValueError("Excel 파일이 비어있습니다.")
            
        df = clean_dataframe(df)
        
        # 필수 컬럼 확인
        required_cols = ['대리점설계사조직코드', '대리점설계사명', '지원매니저코드', '월납환산보험료', '건수']
        missing_cols = [c for c in required_cols if c not in df.columns]
        if missing_cols:
            st.warning(f"필수 컬럼이 없습니다: {missing_cols}")
            st.info("사용 가능한 컬럼들:")
            st.write(list(df.columns))
            
        # 날짜 컬럼 보정
        for col in ["입력일자", "계상일자", "영수일자", "청약일자"]:
            if col in df.columns:
                df[col] = pd.to_datetime(df[col], errors="coerce")
                
        return df
        
    except requests.exceptions.RequestException as e:
        raise ValueError(f"URL 접근 실패: {e}")
    except Exception as e:
        # 상세한 에러 정보 제공
        raise ValueError(f"Excel 파일 읽기 실패: {e}")


# ─────────────────────────────────────────────
# 매니저 단위 필터
# ─────────────────────────────────────────────
def filter_by_manager(df: pd.DataFrame, manager_code: str) -> pd.DataFrame:
    return df[df["지원매니저코드"].astype(str).str.strip() == str(manager_code).strip()].copy()

# ─────────────────────────────────────────────
# LLM: 자연어 → 필터 스펙 (JSON)
# ─────────────────────────────────────────────
SCHEMA_HINT = """
데이터 스키마 (계약 1건 = 1행):
- 대리점설계사조직코드 (str): 사용인(설계사) 코드
- 대리점설계사명 (str): 사용인 이름
- 영업가족명 (str): 대리점명
- 대리점지사명 (str): 지사명
- 월납환산보험료 (int): 실적 금액. 설계사 실적 = 이 값의 합계
- 건수 (int): 계약 건수. 보통 1
- 상품구분 (str): '인보험' | '물보험'
- 상품중분류코드명 (str): 예) '실손의료비보험', '운전자보험', '어린이보험', '걱정없는암보험' 등
- 상품명 (str): 상품 풀네임
- 자기계약여부 (str): 'Y' | 'N'
- 취급자계약여부 (str): 'Y' | 'N'
- 내근직계약여부 (str): 'Y' | 'N'
- 실손담보가입여부 (str): 'Y' | 'N'
- GA주차구분명 (str): '1주차' | '2주차' | '3주차' 등
- 청약채널구분코드명 (str): '대면' | 'TM'
- 청약일자 (date)
- 승환여부 (str): 'Y' | 'N'
"""

SYSTEM_PROMPT = f"""당신은 보험 GA 데이터 조회 어시스턴트입니다.
사용자(지원매니저)의 자연어 요청을 받아서, pandas DataFrame에 적용할 필터와 결과 컬럼을 JSON으로만 반환합니다.

{SCHEMA_HINT}

중요 규칙:
- "가동"은 해당 월 계약 건수가 1건 이상인 설계사를 의미합니다 (데이터에 존재하는 설계사 = 가동).
- "실적"은 설계사별 월납환산보험료 합계입니다.
- 월납환산보험료는 음수일 수 있습니다 (취소/이탈 계약). 사용자가 "X원 미만"이라고 했을 때, 저실적자를 찾는 맥락이면 `between [0, X-1]`로 해석하세요. "마이너스", "음수", "이탈"을 명시한 경우에만 음수 범위를 포함합니다.
- 결과는 설계사 단위로 groupby 집계됩니다.
- 계약 단계 필터(row-level filter)와 설계사 단계 필터(group-level filter, 주로 실적 금액 기준)를 구분해야 합니다.
- 사용자가 "자기계약 빼고", "내근직 제외" 같이 요청하면 row_filters에 추가. 기본은 제외하지 않습니다.
- 금액 단위: "만원" = 10000, "천원" = 1000. 예: "10만원 미만" → 100000 미만.

반드시 다음 JSON 스키마로만 응답하세요 (코드블록이나 설명 없이 순수 JSON):
{{
  "row_filters": [
    {{"column": "컬럼명", "op": "==|!=|in|not_in|contains|>=|<=|>|<", "value": <값 또는 배열>}}
  ],
  "agent_filters": [
    {{"metric": "total_premium|contract_count", "op": ">=|<=|>|<|==|between", "value": <숫자 또는 [min,max]>}}
  ],
  "extra_columns": ["상품중분류코드명", "GA주차구분명"],
  "sort_by": "total_premium|contract_count|none",
  "sort_desc": true,
  "explanation": "한국어로 어떤 조건으로 필터했는지 1-2문장 설명"
}}

- row_filters: 계약(row) 단위 필터. 해당 조건의 계약만 남깁니다.
- agent_filters: 설계사 집계 후 필터. total_premium=월납환산보험료 합, contract_count=건수 합.
- extra_columns: 결과 테이블에 추가로 보여줄 컬럼 (기본 컬럼 외에).
- 필터 조건이 없으면 빈 배열 [].
"""

def parse_prompt_fallback(prompt: str) -> dict:
    """LLM 호출이 실패했을 때 사용할 기본 패턴 매칭"""
    prompt = prompt.lower().strip()
    
    # 기본 스펙
    spec = {
        "row_filters": [],
        "agent_filters": [],
        "extra_columns": [],
        "sort_by": "total_premium",
        "sort_desc": True,
        "explanation": "기본 패턴 매칭으로 해석했습니다."
    }
    
    # 제외 조건들
    if "자기계약" in prompt and ("빼" in prompt or "제외" in prompt):
        spec["row_filters"].append({"column": "자기계약여부", "op": "==", "value": "N"})
    if "내근직" in prompt and ("빼" in prompt or "제외" in prompt):
        spec["row_filters"].append({"column": "내근직계약여부", "op": "==", "value": "N"})
    if "취급자" in prompt and ("빼" in prompt or "제외" in prompt):
        spec["row_filters"].append({"column": "취급자계약여부", "op": "==", "value": "N"})
    
    # 상품 조건들
    if "실손" in prompt:
        spec["row_filters"].append({"column": "실손담보가입여부", "op": "==", "value": "Y"})
        spec["extra_columns"].append("상품중분류코드명")
    if "운전자" in prompt:
        spec["row_filters"].append({"column": "상품중분류코드명", "op": "contains", "value": "운전자"})
        spec["extra_columns"].append("상품중분류코드명")
    
    # 금액 조건들 (간단한 패턴만)
    import re
    amount_patterns = [
        (r"(\d+)만원\s*미만", lambda m: {"metric": "total_premium", "op": "between", "value": [0, int(m.group(1)) * 10000 - 1]}),
        (r"(\d+)만원\s*이상", lambda m: {"metric": "total_premium", "op": ">=", "value": int(m.group(1)) * 10000}),
        (r"(\d+)만원\s*초과", lambda m: {"metric": "total_premium", "op": ">", "value": int(m.group(1)) * 10000}),
    ]
    
    for pattern, handler in amount_patterns:
        match = re.search(pattern, prompt)
        if match:
            spec["agent_filters"].append(handler(match))
            break
    
    return spec

def call_llm(user_prompt: str, api_key: str) -> dict:
    if not ANTHROPIC_AVAILABLE:
        st.warning("Anthropic 라이브러리 사용 불가. 기본 패턴 매칭으로 대체합니다.")
        return parse_prompt_fallback(user_prompt)
    
    if not api_key:
        st.warning("ANTHROPIC_API_KEY가 설정되지 않았습니다. 기본 패턴 매칭으로 대체합니다.")
        return parse_prompt_fallback(user_prompt)
    
    try:
        client = Anthropic(api_key=api_key)
        resp = client.messages.create(
            model="claude-3-5-sonnet-20241022",
            max_tokens=1024,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_prompt}],
        )
        text = resp.content[0].text.strip()
        # ```json ... ``` 같은 코드블록이 섞여올 수 있으니 제거
        text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.MULTILINE).strip()
        return json.loads(text)
    except Exception as e:
        st.warning(f"LLM 호출 실패 ({str(e)[:100]}...). 기본 패턴 매칭으로 대체합니다.")
        return parse_prompt_fallback(user_prompt)

# ─────────────────────────────────────────────
# 필터 스펙 적용
# ─────────────────────────────────────────────
def apply_row_filters(df: pd.DataFrame, filters: list) -> pd.DataFrame:
    out = df.copy()
    for f in filters:
        col, op, val = f.get("column"), f.get("op"), f.get("value")
        if col not in out.columns:
            continue
        s = out[col]
        if op == "==":
            out = out[s == val]
        elif op == "!=":
            out = out[s != val]
        elif op == "in":
            out = out[s.isin(val if isinstance(val, list) else [val])]
        elif op == "not_in":
            out = out[~s.isin(val if isinstance(val, list) else [val])]
        elif op == "contains":
            out = out[s.astype(str).str.contains(str(val), na=False)]
        elif op == ">=":
            out = out[s >= val]
        elif op == "<=":
            out = out[s <= val]
        elif op == ">":
            out = out[s > val]
        elif op == "<":
            out = out[s < val]
    return out

def aggregate_by_agent(df: pd.DataFrame, extra_columns: list) -> pd.DataFrame:
    if df.empty:
        return pd.DataFrame()
    agg = (
        df.groupby(["대리점설계사조직코드", "대리점설계사명", "영업가족명", "대리점지사명"], dropna=False)
        .agg(
            건수=("건수", "sum"),
            월납환산보험료합계=("월납환산보험료", "sum"),
        )
        .reset_index()
    )
    # extra_columns: 설계사별로 고유값들 모아서 문자열로
    for col in extra_columns or []:
        if col in df.columns and col not in agg.columns:
            extra = (
                df.groupby("대리점설계사조직코드")[col]
                .apply(lambda s: ", ".join(sorted({str(x) for x in s.dropna()})))
                .reset_index()
            )
            agg = agg.merge(extra, on="대리점설계사조직코드", how="left")
    return agg

def apply_agent_filters(agg: pd.DataFrame, filters: list) -> pd.DataFrame:
    out = agg.copy()
    for f in filters:
        metric = f.get("metric")
        op = f.get("op")
        val = f.get("value")
        col_map = {"total_premium": "월납환산보험료합계", "contract_count": "건수"}
        col = col_map.get(metric)
        if not col or col not in out.columns:
            continue
        s = out[col]
        if op == ">=":
            out = out[s >= val]
        elif op == "<=":
            out = out[s <= val]
        elif op == ">":
            out = out[s > val]
        elif op == "<":
            out = out[s < val]
        elif op == "==":
            out = out[s == val]
        elif op == "between" and isinstance(val, list) and len(val) == 2:
            out = out[(s >= val[0]) & (s <= val[1])]
    return out

# ─────────────────────────────────────────────
# UI
# ─────────────────────────────────────────────
def login_ui():
    st.title("🔍 사용인 조회")
    st.caption("지원매니저 전용")
    
    # URL 디버깅 정보
    if st.checkbox("URL 디버깅 정보 표시"):
        st.subheader("🔧 설정 확인")
        data_url = _safe_secret("DATA_URL", "설정되지 않음")
        api_key = _safe_secret("ANTHROPIC_API_KEY", "설정되지 않음")
        
        st.text(f"DATA_URL: {data_url}")
        st.text(f"ANTHROPIC_API_KEY: {'설정됨' if api_key and api_key != '설정되지 않음' else '설정되지 않음'}")
        
        if data_url and data_url != "설정되지 않음":
            if st.button("URL 연결 테스트"):
                try:
                    r = requests.get(data_url, timeout=10)
                    st.success(f"연결 성공! Status: {r.status_code}")
                    st.info(f"Content-Type: {r.headers.get('content-type', 'unknown')}")
                    st.info(f"Content-Length: {len(r.content):,} bytes")
                    
                    # 처음 100자 미리보기
                    preview = r.content[:100]
                    if b'<html' in preview.lower():
                        st.error("⚠️ HTML 페이지를 받았습니다. Raw URL이 아닙니다!")
                    elif preview.startswith(b'PK'):  # Excel 파일 시그니처
                        st.success("✅ Excel 파일로 보입니다!")
                    else:
                        st.warning(f"파일 형식 불명: {preview[:50]}...")
                        
                except Exception as e:
                    st.error(f"연결 실패: {e}")
        st.divider()
    
    with st.form("login"):
        code = st.text_input("지원매니저 코드", placeholder="예: 320010154")
        ok = st.form_submit_button("로그인", use_container_width=True)
    if ok and code.strip():
        try:
            df = load_data(DATA_URL)
        except Exception as e:
            st.error(f"데이터 로드 실패: {e}")
            return
        mine = filter_by_manager(df, code.strip())
        if mine.empty:
            st.error(f"'{code}' 매니저 산하 데이터가 없습니다. 코드를 확인해주세요.")
            return
        manager_name = mine["지원매니저명"].dropna().iloc[0] if "지원매니저명" in mine.columns else ""
        st.session_state["manager_code"] = code.strip()
        st.session_state["manager_name"] = manager_name
        st.rerun()

def main_ui():
    code = st.session_state["manager_code"]
    name = st.session_state.get("manager_name", "")

    col1, col2 = st.columns([4, 1])
    with col1:
        st.title(f"🔍 사용인 조회")
        st.caption(f"매니저: {name} ({code})")
    with col2:
        if st.button("로그아웃", use_container_width=True):
            for k in ["manager_code", "manager_name"]:
                st.session_state.pop(k, None)
            st.rerun()

    try:
        df = load_data(DATA_URL)
    except Exception as e:
        st.error(f"데이터 로드 실패: {e}")
        return
    mine = filter_by_manager(df, code)

    # 산하 요약
    n_agents = mine["대리점설계사조직코드"].nunique()
    total_prem = int(mine["월납환산보험료"].sum())
    c1, c2, c3 = st.columns(3)
    c1.metric("산하 가동 설계사", f"{n_agents:,}명")
    c2.metric("총 계약 건수", f"{int(mine['건수'].sum()):,}건")
    c3.metric("월납환산보험료 합계", f"{total_prem:,}원")

    st.divider()

    st.subheader("💬 자연어로 조회하기")
    st.caption("예시: '이번달 가동했는데 10만원 미만인 사용인', '실손 가입자만', '자기계약 빼고 30만원 이상인 사용인'")

    prompt = st.text_area("요청을 입력하세요", height=80, key="prompt")
    run = st.button("조회", type="primary", use_container_width=True)

    if run and prompt.strip():
        with st.spinner("프롬프트 해석 중..."):
            try:
                spec = call_llm(prompt.strip(), ANTHROPIC_API_KEY)
            except Exception as e:
                st.error(f"프롬프트 해석 실패: {e}")
                st.info("기본 패턴 매칭을 시도합니다...")
                spec = parse_prompt_fallback(prompt.strip())

        with st.expander("🔧 해석된 조건 보기"):
            st.json(spec)
        if spec.get("explanation"):
            st.info(spec["explanation"])

        filtered_rows = apply_row_filters(mine, spec.get("row_filters", []))
        agg = aggregate_by_agent(filtered_rows, spec.get("extra_columns", []))
        agg = apply_agent_filters(agg, spec.get("agent_filters", []))

        sort_by = spec.get("sort_by", "total_premium")
        sort_map = {"total_premium": "월납환산보험료합계", "contract_count": "건수"}
        if sort_by in sort_map and sort_map[sort_by] in agg.columns:
            agg = agg.sort_values(sort_map[sort_by], ascending=not spec.get("sort_desc", True))

        st.success(f"조회 결과: {len(agg):,}명")
        if not agg.empty:
            # 컬럼 순서 정리
            base_cols = ["대리점설계사명", "대리점설계사조직코드", "영업가족명", "대리점지사명", "건수", "월납환산보험료합계"]
            other_cols = [c for c in agg.columns if c not in base_cols]
            agg = agg[base_cols + other_cols]
            st.dataframe(
                agg,
                use_container_width=True,
                hide_index=True,
                column_config={
                    "월납환산보험료합계": st.column_config.NumberColumn(format="%d 원"),
                    "건수": st.column_config.NumberColumn(format="%d 건"),
                },
            )
            csv = agg.to_csv(index=False).encode("utf-8-sig")
            st.download_button("CSV 다운로드", csv, f"조회결과_{datetime.now():%Y%m%d_%H%M%S}.csv", "text/csv")

# ─────────────────────────────────────────────
# Entry
# ─────────────────────────────────────────────
if "manager_code" not in st.session_state:
    login_ui()
else:
    main_ui()
