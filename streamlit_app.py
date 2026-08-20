"""
은퇴와 상속설계 1주차 - 5대 이슈 라이브 웹앱
Streamlit + Supabase

배포 전 준비
 1) Supabase에서 supabase_schema.sql 실행
 2) Streamlit Cloud > Settings > Secrets 에 아래 두 줄 입력
      SUPABASE_URL = "https://xxxx.supabase.co"
      SUPABASE_KEY = "eyJhbGci..."   # anon public key
 3) requirements.txt : streamlit, pandas, supabase
"""

import streamlit as st
import pandas as pd
import random
import json
from supabase import create_client, Client

st.set_page_config(page_title="은퇴설계 5대 이슈", page_icon="🧭", layout="wide")

PROF_PW = "3383"
CLASSES = ["인하대", "숙대1", "숙대2"]
PHASES = ["대기", "학생입장", "사전투표", "체험", "결과", "사후투표", "종료"]

ISSUES = {
    1: {
        "title": "늘어난 수명, 더 모을 것인가 더 오래 일할 것인가",
        "A": "더 모아라 (금융자산 축적)",
        "B": "더 오래 일하라 (무형자산·다단계 인생)",
        "play": "100세 생존 시뮬레이터",
    },
    2: {
        "title": "미래의 나 vs 지금의 경험",
        "A": "퓨처셀프 (미래의 나와 연결)",
        "B": "다잉 위드 제로 (경험의 골든타임)",
        "play": "나의 현재편향 측정소",
    },
    3: {
        "title": "20대의 첫 목돈, 잠글 것인가 열어둘 것인가",
        "A": "연금계좌에 잠가라",
        "B": "유동성이 먼저다",
        "play": "첫 목돈 1,000만원 인생 서바이벌",
    },
    4: {
        "title": "노후의 중심, 내 집인가 금융자산인가",
        "A": "내 집 한 채 + 주택연금",
        "B": "금융자산 중심 (주거는 비용)",
        "play": "집 vs 금융자산 30년 대결",
    },
    5: {
        "title": "은퇴설계, AI에게 맡길 것인가 사람에게 맡길 것인가",
        "A": "AI(로보어드바이저) 중심",
        "B": "사람(전문가 또는 나) 중심",
        "play": "AI vs 사람 블라인드 테스트",
    },
}


# ==========================================================
# 공통 유틸
# ==========================================================
@st.cache_resource
def init_connection() -> Client:
    return create_client(st.secrets["SUPABASE_URL"], st.secrets["SUPABASE_KEY"])


supabase: Client = init_connection()


@st.cache_data(ttl=4, show_spinner=False)
def _fetch_status(class_name: str):
    """4초간 캐시. 슬라이더 조작으로 스크립트가 재실행돼도 DB를 다시 때리지 않는다."""
    return supabase.table("issue_status").select("*").eq("class_name", class_name).execute().data


def get_status(class_name: str) -> dict:
    data = _fetch_status(class_name)
    if not data:
        supabase.table("issue_status").insert({"class_name": class_name}).execute()
        _fetch_status.clear()
        return {"class_name": class_name, "current_issue": 0, "current_phase": "대기",
                "scenario_seed": 1234, "reveal": False}
    return data[0]


def set_status(class_name: str, **kwargs):
    supabase.table("issue_status").update(kwargs).eq("class_name", class_name).execute()
    _fetch_status.clear()


def save_response(class_name, name, issue_no, stage, choice=None, score=0.0, payload=None):
    """같은 학생·이슈·단계는 덮어쓰기(upsert)."""
    row = {
        "class_name": class_name, "name": name, "issue_no": issue_no, "stage": stage,
        "choice": choice, "score": float(score),
        "payload": json.dumps(payload, ensure_ascii=False) if payload else None,
    }
    supabase.table("issue_responses").upsert(
        row, on_conflict="class_name,name,issue_no,stage"
    ).execute()


def load_responses(class_name, issue_no=None, stage=None) -> pd.DataFrame:
    q = supabase.table("issue_responses").select("*").eq("class_name", class_name)
    if issue_no:
        q = q.eq("issue_no", issue_no)
    if stage:
        q = q.eq("stage", stage)
    return pd.DataFrame(q.execute().data)


def my_response(class_name, name, issue_no, stage):
    res = (supabase.table("issue_responses").select("*")
           .eq("class_name", class_name).eq("name", name)
           .eq("issue_no", issue_no).eq("stage", stage).execute())
    return res.data[0] if res.data else None


# ==========================================================
# 시뮬레이션 로직 (교수·학생 화면 공용)
# ==========================================================
def simulate_depletion(start_age, retire_age, monthly_save_manwon, ret_rate, spend_manwon, infl=0.02):
    """이슈1: 저축 → 은퇴자산 → 인출. 자산이 0이 되는 나이를 반환(120 = 고갈 안 됨).
    생활비는 '오늘 기준 금액'으로 입력받아 은퇴 시점 물가로 환산한다."""
    assets = 0.0
    r_m = ret_rate / 12
    for _ in range((retire_age - start_age) * 12):
        assets = assets * (1 + r_m) + monthly_save_manwon
    age = retire_age
    spend = spend_manwon * ((1 + infl) ** (retire_age - start_age))  # 물가 반영
    while age < 120 and assets > 0:
        for _ in range(12):
            assets = assets * (1 + r_m) - spend
            if assets <= 0:
                break
        spend *= (1 + infl)
        age += 1
    return min(age, 120), assets


def implied_discount_rate(answers):
    """이슈2: '지금 100만원 vs 1년 뒤 X만원' 응답에서 개인 할인율 추정."""
    # answers: [(future_amount, chose_now(bool)), ...]
    accepted = [amt for amt, now in answers if not now]     # 미래를 택한 금액들
    rejected = [amt for amt, now in answers if now]          # 지금을 택한 금액들
    if not accepted:
        return 100.0
    if not rejected:
        return (min(accepted) - 100) / 100 * 100
    return (min(accepted) - 100) / 100 * 100


def simulate_first_money(seed, pension, isa, emergency):
    """이슈3: 1,000만원을 3곳에 배분한 뒤 30년간 인생 이벤트를 겪는다."""
    rng = random.Random(seed)
    logs = []
    p, i, e = float(pension), float(isa), float(emergency)
    penalty_total = 0.0
    events = [
        ("이직 공백 3개월", 300), ("대학원 등록금", 500), ("결혼 자금", 800),
        ("부모님 병원비", 400), ("전세 보증금 인상", 600), ("창업 초기자금", 700),
    ]
    picked = rng.sample(events, 3)
    for year, (label, need) in zip(sorted(rng.sample(range(3, 25), 3)), picked):
        # 그 시점까지 수익률 반영
        p *= (1.05 ** 1); i *= (1.04 ** 1)
        pay = min(e, need); e -= pay; short = need - pay
        if short > 0:
            take = min(i, short); i -= take; short -= take
        if short > 0:                       # 연금계좌 중도해지 → 기타소득세 16.5%
            take = min(p, short / (1 - 0.165))
            p -= take
            penalty_total += take * 0.165
            short -= take * (1 - 0.165)
        if short > 0:                       # 그래도 모자라면 고금리 대출
            penalty_total += short * 0.20
            logs.append(f"{year}년차 · {label}({need}만원) → 대출 발생, 이자비용 {short*0.2:,.0f}만원")
        else:
            logs.append(f"{year}년차 · {label}({need}만원) → 자체 해결")
    # 남은 기간 복리
    p *= (1.05 ** 20) * 1.132              # 세액공제 재투자 효과 근사
    i *= (1.04 ** 20)
    total = p + i + e - penalty_total
    return total, logs


def simulate_house_vs_fin(seed, choice, down_payment=20000):
    """이슈4: 30년 뒤 순자산 비교(만원 단위). choice: 'A'=집, 'B'=금융자산."""
    rng = random.Random(seed)
    house_growth = rng.uniform(-0.01, 0.05)     # 지역 운
    market_return = rng.uniform(0.03, 0.09)
    rent_infl = rng.uniform(0.01, 0.04)
    if choice == "A":
        value = down_payment * 2.5 * ((1 + house_growth) ** 30)   # 레버리지 포함 주택가치
        debt = down_payment * 1.5 * 0.3                            # 30년 상환 후 잔존부채
        net = value - debt
        liquid = 0
    else:
        net = down_payment * ((1 + market_return) ** 30)
        rent_cost = sum(80 * 12 * ((1 + rent_infl) ** y) for y in range(30))
        net -= rent_cost * 0.35                                    # 자가 대비 추가 주거비 근사
        liquid = net
    return net, liquid, {"집값상승률": house_growth, "시장수익률": market_return, "전월세상승률": rent_infl}


# 이슈5 블라인드 테스트용 조언 카드 (라벨은 화면에서 숨김)
ADVICE_CARDS = [
    {
        "q": "26세 사회초년생입니다. 월 60만원을 어디에 넣어야 할까요?",
        "one": ("IRP와 연금저축에 최대한 넣어 세액공제를 먼저 확보하고, 남는 금액은 "
                "글로벌 지수형 상품으로 자동이체하세요. 55세까지 유지하는 것이 핵심입니다.", "AI"),
        "two": ("먼저 3개월치 생활비가 통장에 있는지부터 확인하세요. 없다면 그것부터 채우고, "
                "이직이나 대학원 계획이 있다면 연금계좌 비중은 절반 이하로 두는 게 안전합니다.", "사람"),
    },
    {
        "q": "시장이 20% 떨어졌습니다. 지금 팔아야 할까요?",
        "one": ("역사적으로 하락 후 회복까지 평균 약 2년이 걸렸습니다. 장기투자자라면 "
                "매도보다 리밸런싱이 통계적으로 우월한 선택입니다.", "AI"),
        "two": ("지금 잠이 안 오시나요? 그렇다면 비중이 본인 성향보다 컸다는 뜻입니다. "
                "전부 팔지 말고, 잠들 수 있는 수준까지만 줄이세요.", "사람"),
    },
    {
        "q": "부모님이 주택연금 가입을 고민 중입니다.",
        "one": ("72세·4억 주택 기준 월 약 134만원 수령이 가능합니다. 종신지급·비소구 구조이므로 "
                "현금흐름 관점에서 합리적입니다.", "AI"),
        "two": ("숫자보다 먼저 가족회의를 하세요. 주택연금은 사실상 그 집을 물려주지 않겠다는 "
                "결정이고, 갈등의 90%는 그 합의가 없을 때 생깁니다.", "사람"),
    },
]


# ==========================================================
# 로그인
# ==========================================================
if "role" not in st.session_state:
    st.title("🧭 은퇴설계 5대 이슈 라이브")
    st.caption("은퇴와 상속설계 · 1주차")
    role = st.radio("접속 유형", ["학생", "교수"], horizontal=True)

    if role == "학생":
        st.info("교수님이 방을 열면 이름만 입력하여 자동으로 입장합니다.")
        name = st.text_input("이름을 입력하세요")
        if st.button("입장하기", type="primary"):
            if not name.strip():
                st.error("이름을 입력해주세요.")
            else:
                rows = supabase.table("issue_status").select("*").execute().data
                active = [r["class_name"] for r in rows if r["current_phase"] not in ("대기", "종료")]
                if len(active) == 1:
                    cn = active[0]
                    exists = (supabase.table("issue_students").select("id")
                              .eq("class_name", cn).eq("name", name).execute().data)
                    if not exists:
                        supabase.table("issue_students").insert(
                            {"class_name": cn, "name": name}).execute()
                    st.session_state.update(role="student", name=name, class_name=cn)
                    st.rerun()
                elif not active:
                    st.error("현재 열려있는 강의실이 없습니다. 교수님이 접속을 허용할 때까지 기다려주세요.")
                else:
                    st.error("여러 분반이 동시에 활성화되어 있습니다. 교수님께 문의하세요.")
    else:
        cn = st.selectbox("분반 선택", CLASSES)
        pw = st.text_input("비밀번호", type="password")
        if st.button("교수 통제소 입장", type="primary"):
            if pw == PROF_PW:
                st.session_state.update(role="professor", class_name=cn)
                st.rerun()
            else:
                st.error("비밀번호가 틀렸습니다.")
    st.stop()


# ==========================================================
# 공통 헤더
# ==========================================================
my_class = st.session_state.class_name
status = get_status(my_class)
issue_no = status["current_issue"]
phase = status["current_phase"]
seed = status["scenario_seed"]
reveal = status["reveal"]
info = ISSUES.get(issue_no)

c1, c2 = st.columns([8, 2])
with c1:
    if info:
        st.markdown(f"### 🏫 [{my_class}] · 이슈 {issue_no}. {info['title']}")
    else:
        st.markdown(f"### 🏫 [{my_class}] 강의실")
with c2:
    if st.button("로그아웃"):
        st.session_state.clear()
        st.rerun()
st.write("---")


# ==========================================================
# 학생 화면
# ==========================================================
if st.session_state.role == "student":
    me = st.session_state.name
    if st.button("🔄 화면 새로고침", type="primary", use_container_width=True):
        st.rerun()
    st.write("")

    if phase == "학생입장" or issue_no == 0:
        st.info(f"{me}님, 접속되었습니다. 교수님이 첫 이슈를 열 때까지 기다려 주세요.")
        st.stop()

    A, B = info["A"], info["B"]

    # ---------- 사전투표 ----------
    if phase == "사전투표":
        st.subheader("① 사전 투표 — 지금 나의 입장은?")
        prev = my_response(my_class, me, issue_no, "pre")
        pick = st.radio("입장을 선택하세요", [A, B],
                        index=0 if not prev else (0 if prev["choice"] == "A" else 1))
        reason = st.text_input("한 줄 근거 (선택)", value="")
        if st.button("투표 제출", type="primary"):
            save_response(my_class, me, issue_no, "pre",
                          choice="A" if pick == A else "B", payload={"reason": reason})
            st.success("제출되었습니다. 새로고침 후 대기하세요.")

    # ---------- 체험 ----------
    elif phase == "체험":
        st.subheader(f"② 체험 — {info['play']}")

        if issue_no == 1:
            @st.fragment
            def sim_fragment():
                """슬라이더 조작은 이 블록만 재실행된다(전체 스크립트·DB 재조회 없음)."""
                st.write("나의 은퇴 시나리오를 입력하면, **내 돈이 몇 살에 바닥나는지** 계산합니다.")
                col = st.columns(4)
                retire_age = col[0].slider("은퇴 나이", 55, 80, 65)
                save = col[1].slider("월 저축액(만원)", 10, 200, 50, step=10)
                spend = col[2].slider("은퇴 후 월 생활비(만원)", 100, 400, 200, step=10)
                rate = col[3].slider("연 수익률(%)", 1.0, 8.0, 5.0, step=0.5)

                dep_age, _ = simulate_depletion(25, retire_age, save, rate / 100, spend)
                base_age, _ = simulate_depletion(25, 65, 50, 0.05, 200)  # 기본 시나리오

                if dep_age >= 120:
                    st.success("자산이 100세까지 고갈되지 않습니다.")
                elif dep_age >= 100:
                    st.success(f"자산 고갈 예상: **{dep_age}세** — 100세까지 버팁니다.")
                else:
                    st.error(f"자산 고갈 예상: **{dep_age}세** — 100세까지 **{100-dep_age}년이 빕니다.**")
                st.caption(f"기본 시나리오(65세 은퇴·월 50만원 저축·생활비 200만원)는 {base_age}세 고갈")

                if st.button("내 결과 제출", type="primary"):
                    save_response(my_class, me, issue_no, "play", score=dep_age,
                                  payload={"retire": retire_age, "save": save,
                                           "spend": spend, "rate": rate})
                    st.success("제출 완료!")

            sim_fragment()

        elif issue_no == 2:
            st.write("**지금 100만원**과 **1년 뒤 아래 금액** 중 무엇을 택하시겠습니까?")
            offers = [105, 110, 120, 140, 180]
            answers = []
            for amt in offers:
                pick = st.radio(f"1년 뒤 {amt}만원", ["지금 100만원", f"1년 뒤 {amt}만원"],
                                horizontal=True, key=f"d{amt}")
                answers.append((amt, pick.startswith("지금")))
            letter = st.text_area("60세의 나에게 한 문장을 남긴다면?", height=80)
            if st.button("제출", type="primary"):
                r = implied_discount_rate(answers)
                save_response(my_class, me, issue_no, "play", score=r, payload={"letter": letter})
                st.success(f"제출 완료! 추정된 나의 연 할인율은 약 **{r:.0f}%** 입니다. "
                           "(시중금리보다 훨씬 높다면 그것이 현재편향입니다)")

        elif issue_no == 3:
            st.write("첫 목돈 **1,000만원**을 세 곳에 나눠 담으세요. 30년간 인생 이벤트가 발생합니다.")
            pension = st.slider("연금계좌 (세액공제·과세이연, 55세 잠금)", 0, 1000, 400, step=100)
            isa = st.slider("ISA·중기계좌 (중간 인출 가능)", 0, 1000 - pension, min(300, 1000 - pension), step=100)
            emergency = 1000 - pension - isa
            st.info(f"비상금(수시입출): **{emergency}만원**")
            if st.button("30년 돌려보기", type="primary"):
                total, logs = simulate_first_money(seed, pension, isa, emergency)
                for l in logs:
                    st.write("· " + l)
                st.metric("30년 뒤 최종 자산", f"{total:,.0f} 만원")
                save_response(my_class, me, issue_no, "play", score=total,
                              payload={"pension": pension, "isa": isa, "emergency": emergency})

        elif issue_no == 4:
            st.write("30년 뒤를 향해 한 쪽을 고르세요. 시장 시나리오는 **분반 전체가 동일**합니다.")
            pick = st.radio("나의 선택", [A, B])
            if st.button("30년 돌려보기", type="primary"):
                ch = "A" if pick == A else "B"
                net, liquid, sc = simulate_house_vs_fin(seed, ch)
                st.metric("30년 뒤 순자산", f"{net:,.0f} 만원")
                st.caption(f"유동성(즉시 현금화 가능): {liquid:,.0f} 만원")
                save_response(my_class, me, issue_no, "play", choice=ch, score=net,
                              payload=sc)
                st.info("결과 발표 때 시나리오가 공개됩니다.")

        elif issue_no == 5:
            st.write("아래 두 조언 중 **어느 쪽이 AI의 조언**일까요? 그리고 **어느 쪽을 따르겠습니까?**")
            picks = {}
            for idx, card in enumerate(ADVICE_CARDS):
                st.markdown(f"**Q{idx+1}. {card['q']}**")
                cc = st.columns(2)
                cc[0].info("**조언 ①**\n\n" + card["one"][0])
                cc[1].warning("**조언 ②**\n\n" + card["two"][0])
                picks[f"q{idx+1}_ai"] = st.radio("AI가 쓴 것은?", ["①", "②"],
                                                 horizontal=True, key=f"ai{idx}")
                picks[f"q{idx+1}_follow"] = st.radio("내가 따를 조언은?", ["①", "②"],
                                                     horizontal=True, key=f"fo{idx}")
                st.write("---")
            if st.button("제출", type="primary"):
                correct = sum(1 for i in range(len(ADVICE_CARDS)) if picks[f"q{i+1}_ai"] == "①")
                save_response(my_class, me, issue_no, "play", score=correct, payload=picks)
                st.success("제출 완료! 정답은 교수님 화면에서 함께 공개됩니다.")

    # ---------- 결과 ----------
    elif phase == "결과":
        st.subheader("③ 결과 공개")
        st.info("교수님 화면(스크린)을 함께 보세요.")
        if issue_no == 5 and reveal:
            st.write("**정답: 모든 문항에서 조언 ①이 AI, 조언 ②가 사람입니다.**")
            st.caption("AI는 평균과 통계로 답하고, 사람은 당신의 상황과 감정을 먼저 묻습니다.")

    # ---------- 사후투표 ----------
    elif phase == "사후투표":
        st.subheader("④ 사후 투표 — 생각이 바뀌었나요?")
        pre = my_response(my_class, me, issue_no, "pre")
        if pre:
            st.caption(f"나의 사전 입장: {A if pre['choice']=='A' else B}")
        pick = st.radio("지금 나의 입장", [A, B])
        why = st.text_input("바꿨다면/유지했다면 그 이유는?")
        if st.button("최종 제출", type="primary"):
            save_response(my_class, me, issue_no, "post",
                          choice="A" if pick == A else "B", payload={"why": why})
            st.success("제출 완료! 수고하셨습니다.")

    elif phase == "종료":
        st.success("오늘 5대 이슈 세션이 모두 끝났습니다. 수고하셨습니다!")
        df = load_responses(my_class, stage="post")
        if not df.empty:
            mine = df[df["name"] == me]
            st.write(f"내가 참여한 이슈 수: {len(mine)}개")


# ==========================================================
# 교수 통제소
# ==========================================================
else:
    top = st.columns([2, 6, 2])
    if top[0].button("🔄 현황 새로고침", type="primary"):
        st.rerun()
    top[1].markdown(f"**현재 상태:** 이슈 {issue_no} · {phase}")
    n_students = len(supabase.table("issue_students").select("id")
                     .eq("class_name", my_class).execute().data)
    top[2].metric("접속 학생", f"{n_students} 명")
    st.write("---")

    ctl = st.columns([3, 3, 2, 2])
    new_issue = ctl[0].selectbox("이슈 선택", [0, 1, 2, 3, 4, 5],
                                 index=issue_no,
                                 format_func=lambda x: "— 대기 —" if x == 0 else f"이슈 {x}. {ISSUES[x]['title'][:20]}")
    new_phase = ctl[1].selectbox("단계", PHASES, index=PHASES.index(phase))
    if ctl[2].button("✅ 적용", type="primary", use_container_width=True):
        set_status(my_class, current_issue=new_issue, current_phase=new_phase, reveal=False)
        st.rerun()
    if ctl[3].button("🎲 시나리오 재추첨", use_container_width=True):
        set_status(my_class, scenario_seed=random.randint(1, 99999))
        st.rerun()

    st.write("---")

    if issue_no == 0:
        st.info("이슈를 선택하고 단계를 '학생입장'으로 바꾸면 학생들이 접속할 수 있습니다.")
    else:
        A, B = info["A"], info["B"]
        pre_df = load_responses(my_class, issue_no, "pre")
        play_df = load_responses(my_class, issue_no, "play")
        post_df = load_responses(my_class, issue_no, "post")

        # 사전/사후 투표 비교
        st.subheader("📊 사전 · 사후 투표 현황")
        cols = st.columns(2)
        for label, df, col in [("사전", pre_df, cols[0]), ("사후", post_df, cols[1])]:
            with col:
                if df.empty:
                    st.write(f"{label} 투표: 아직 없음")
                else:
                    a = int((df["choice"] == "A").sum())
                    b = int((df["choice"] == "B").sum())
                    st.write(f"**{label} 투표 ({a+b}명)**")
                    st.bar_chart(pd.DataFrame({"표": [a, b]}, index=[f"A. {A}", f"B. {B}"]))

        if not pre_df.empty and not post_df.empty:
            merged = pre_df[["name", "choice"]].merge(
                post_df[["name", "choice"]], on="name", suffixes=("_pre", "_post"))
            changed = merged[merged["choice_pre"] != merged["choice_post"]]
            st.success(f"**입장을 바꾼 학생: {len(changed)}명 / {len(merged)}명** "
                       f"— 이 학생들을 지목해 이유를 물어보세요.")
            if not changed.empty:
                st.write(", ".join(changed["name"].tolist()))

        st.write("---")
        st.subheader(f"🎮 체험 결과 — {info['play']}")

        if play_df.empty:
            st.write("아직 제출된 결과가 없습니다.")
        else:
            play_df["payload_d"] = play_df["payload"].apply(
                lambda x: json.loads(x) if x else {})

            if issue_no == 1:
                st.write("**자산 고갈 나이 분포** (100세 미만이면 노후 파산)")
                st.bar_chart(play_df.set_index("name")["score"])
                fail = int((play_df["score"] < 100).sum())
                st.error(f"100세 이전 자산 고갈: **{fail}명 / {len(play_df)}명**")
                st.dataframe(pd.DataFrame({
                    "이름": play_df["name"],
                    "은퇴나이": [p.get("retire") for p in play_df["payload_d"]],
                    "월저축": [p.get("save") for p in play_df["payload_d"]],
                    "월생활비": [p.get("spend") for p in play_df["payload_d"]],
                    "고갈나이": play_df["score"].astype(int),
                }).sort_values("고갈나이"), use_container_width=True)

            elif issue_no == 2:
                st.write("**추정 개인 할인율 분포(%)** — 시중금리(약 3%)와 비교해 보세요")
                st.bar_chart(play_df.set_index("name")["score"])
                st.metric("반 평균 할인율", f"{play_df['score'].mean():.1f} %")
                st.write("**60세의 나에게 남긴 한 문장**")
                for _, r in play_df.iterrows():
                    letter = r["payload_d"].get("letter", "").strip()
                    if letter:
                        st.write(f"· *{r['name']}* — {letter}")

            elif issue_no == 3:
                st.write("**30년 뒤 최종 자산 순위(만원)**")
                rank = play_df.sort_values("score", ascending=False)
                st.dataframe(pd.DataFrame({
                    "이름": rank["name"],
                    "연금계좌": [p.get("pension") for p in rank["payload_d"]],
                    "ISA": [p.get("isa") for p in rank["payload_d"]],
                    "비상금": [p.get("emergency") for p in rank["payload_d"]],
                    "최종자산": rank["score"].round(0).astype(int),
                }), use_container_width=True)
                st.info("비상금 0원으로 간 학생과 400만원 이상 둔 학생을 비교해 보여주세요.")

            elif issue_no == 4:
                _, _, sc = simulate_house_vs_fin(seed, "A")
                st.write(f"**이번 시나리오** — 집값 상승률 {sc['집값상승률']*100:.1f}% / "
                         f"시장수익률 {sc['시장수익률']*100:.1f}% / 전월세 상승률 {sc['전월세상승률']*100:.1f}%")
                grp = play_df.groupby("choice")["score"].agg(["count", "mean"])
                st.dataframe(grp.rename(columns={"count": "인원", "mean": "평균 순자산(만원)"}),
                             use_container_width=True)
                st.caption("재추첨 버튼으로 시나리오를 3~4번 바꿔 돌리면 '운의 영향'이 드러납니다.")

            elif issue_no == 5:
                st.metric("AI 판별 평균 정답 수", f"{play_df['score'].mean():.1f} / 3")
                if st.button("🎉 정답 공개 (학생 화면에 표시)", type="primary"):
                    set_status(my_class, reveal=True)
                    st.rerun()
                follows = []
                for p in play_df["payload_d"]:
                    follows += [v for k, v in p.items() if k.endswith("follow")]
                if follows:
                    ai_follow = follows.count("①")
                    st.write(f"**따르겠다고 선택한 조언:** AI(①) {ai_follow}회 / "
                             f"사람(②) {len(follows)-ai_follow}회")

        st.write("---")
        with st.expander("⚠️ 데이터 관리"):
            if st.button("이 이슈의 응답만 삭제"):
                supabase.table("issue_responses").delete().eq(
                    "class_name", my_class).eq("issue_no", issue_no).execute()
                st.rerun()
            if st.button("이 분반 전체 초기화 (학생 명단 포함)"):
                supabase.table("issue_responses").delete().eq("class_name", my_class).execute()
                supabase.table("issue_students").delete().eq("class_name", my_class).execute()
                set_status(my_class, current_issue=0, current_phase="대기", reveal=False)
                st.rerun()
