# 사용인 조회 앱

지원매니저가 자연어로 본인 산하 사용인(설계사)을 조회하는 Streamlit 앱.

## 동작

1. 매니저코드로 로그인
2. 자연어 프롬프트 입력 → Claude API가 필터 조건으로 해석
3. 본인 산하 데이터에 필터 적용 → 설계사 단위 집계 결과 표시

프롬프트 예시
- "이번달 가동했는데 10만원 미만인 사용인을 보여줘"
- "실손 가입자만"
- "자기계약 빼고 30만원 이상인 사용인"
- "운전자보험 판 설계사"
- "1주차에 실적 없는 사용인" (주의: 데이터에 없는 사람은 조회 불가. 가동한 사람 중 조건만 가능)

## 파일 구조 (GitHub repo)

```
.
├── app.py
├── requirements.txt
├── .streamlit/
│   └── secrets.toml   # 로컬 개발용, gitignore 권장
└── data/
    └── prizebase_YYYYMM.xlsx   # 매일 업데이트
```

## 배포 순서

1. **새 GitHub repo 생성** (private 권장)
2. 위 파일들 push. `data/prizebase_202604.xlsx`도 포함.
3. **Streamlit Cloud**에서 repo 연결 후 배포
4. Streamlit Cloud > App settings > **Secrets** 에 다음 입력:

```toml
DATA_URL = "https://raw.githubusercontent.com/<유저명>/<repo명>/main/data/prizebase_202604.xlsx"
ANTHROPIC_API_KEY = "sk-ant-..."
```

5. **private repo** 인 경우 raw URL에 인증이 필요하므로,
   - 방법 A: repo 를 public 으로 두되 Streamlit 앱 자체에 비밀번호 걸기
   - 방법 B: GitHub Personal Access Token 을 DATA_URL 에 포함
     `https://<TOKEN>@raw.githubusercontent.com/...` 형태
   - 방법 C: GitHub API 로 Authorization 헤더 사용 (app.py 수정 필요)

## 데이터 일일 업데이트

매일 새 데이터를 만들 때:
- 파일명을 `prizebase_YYYYMM.xlsx` 규칙으로 유지
- 월이 바뀌면 `DATA_URL` secret 의 파일명도 바꿔주면 됨
- Streamlit 캐시는 1시간 TTL. 즉시 반영하려면 앱에서 우측 상단 메뉴 → Clear cache

## 로컬 테스트

```bash
pip install -r requirements.txt
mkdir -p .streamlit
# .streamlit/secrets.toml 에 DATA_URL, ANTHROPIC_API_KEY 입력
streamlit run app.py
```
