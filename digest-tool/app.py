"""Streamlit UI: pick a person and a day, read their digest and why each item is there.

Run:  streamlit run app.py
"""
import altair as alt
import pandas as pd
import streamlit as st

from digest_tool import config
from digest_tool.digest import build_digest, load_cache, pretty_day, team_pulse
from digest_tool.feedback import record_feedback, reset_feedback, type_preferences, votes_by
from digest_tool.notebook import load_notebook, state_as_of
from digest_tool.ranker import CATEGORY_LABEL, focus_scores

st.set_page_config(page_title="Daily Digest", page_icon="📬", layout="wide")

# Fixed categorical order (validated palette), with separate steps for the dark surface.
# Colors are tied to a part for the whole timeline, so dragging the day slider never repaints a line.
SERIES_COLORS = {"light": ["#2a78d6", "#eb6834", "#1baf7a", "#eda100"],
                 "dark": ["#3987e5", "#d95926", "#199e70", "#c98500"]}


@st.cache_data
def get_data():
    # The app never calls an LLM itself: it reads whatever the cache has, keywords otherwise.
    timeline, team = load_notebook("none")
    return timeline, team, load_cache()


timeline, team, digest_cache = get_data()
days = sorted(timeline)
people = {p["name"]: p for p in team["people"]}
names = {p["id"]: p["name"].split()[0] for p in team["people"]}
role_label = lambda p: p["role"].replace("_", " ")


def feedback_buttons(item, person, day):
    """👍/👎 for one item. Votes are per item type: they change how much that type counts for this person."""
    vote = votes_by(person["id"]).get((day, item["thread_ts"]))
    if vote:
        st.caption("You marked this " + ("👍 useful" if vote > 0 else "👎 not useful") + ".")
        return
    a, b, _ = st.columns([1, 1, 8])
    key = f"{person['id']}:{day}:{item['thread_ts']}"
    label = CATEGORY_LABEL.get(item["category"], item["category"]).lower()
    if a.button("👍", key="up" + key, help=f"Useful: show me more {label}"):
        record_feedback(person["id"], day, item["thread_ts"], item["category"], +1)
        st.rerun()
    if b.button("👎", key="down" + key, help=f"Not useful: show me fewer {label}"):
        record_feedback(person["id"], day, item["thread_ts"], item["category"], -1)
        st.rerun()


def show_pulse(pulse_items, person=None, day=None):
    """Team Pulse: the same items for everyone. With a person, also say why it matters to them."""
    st.markdown("#### 🧭 Team Pulse")
    st.caption("The same items for everyone on the team, so everyone works from the same picture.")
    if not pulse_items:
        st.caption("Nothing team-wide today.")
    for item in pulse_items:
        with st.container(border=True):
            st.markdown(f"**{item['label']}**  ·  `#{item['channel']}`")
            st.write(item["summary"])
            st.caption("\n".join(f"- {r}" for r in item["reasons"]))
            if person and item.get("for_you"):
                st.caption("**For you:** " + "; ".join(item["for_you"]))
            if person:
                feedback_buttons(item, person, day)


def show_for_you(d, person, day, with_feedback=True):
    st.markdown("#### 👤 For You")
    if not d["for_you"]:
        st.caption("Nothing else for you today.")
    for item in d["for_you"]:
        with st.container(border=True):
            flag = "  ·  ⚑ **Don't skip**" if item["must"] else ""
            st.markdown(f"**{item['label']}**  ·  `#{item['channel']}`{flag}")
            st.write(item["summary"])
            st.caption("\n".join(f"- {r}" for r in item["reasons"]))
            if with_feedback:
                feedback_buttons(item, person, day)


def focus_chart(person, day):
    """Decayed focus per part for every day; the selected day is marked with a rule."""
    rows = []
    for d in days:
        for part, value in focus_scores(person["id"], state_as_of(timeline, d), d).items():
            rows.append({"day": pd.Timestamp(d), "part": part, "focus": round(value, 2)})
    if not rows:
        st.caption("No part activity for this person yet.")
        return
    df = pd.DataFrame(rows)
    colors = SERIES_COLORS["dark" if st.context.theme.type == "dark" else "light"]
    # Top 4 parts over the whole period; more lines than that stops being readable.
    top = df.groupby("part")["focus"].sum().nlargest(len(colors)).index.tolist()
    df = df[df["part"].isin(top)]
    color = alt.Color("part:N", title=None, scale=alt.Scale(domain=top, range=colors[:len(top)]),
                      legend=alt.Legend(orient="top"))
    base = alt.Chart(df).encode(
        x=alt.X("day:T", title=None, axis=alt.Axis(format="%b %d", grid=False)),
        y=alt.Y("focus:Q", title="focus (decayed mentions)", axis=alt.Axis(gridOpacity=0.3)),
        color=color,
        tooltip=[alt.Tooltip("day:T", format="%a %b %d"), "part:N", "focus:Q"],
    )
    lines = base.mark_line(strokeWidth=2) + base.mark_point(size=40, filled=True)
    rule = alt.Chart(pd.DataFrame({"day": [pd.Timestamp(day)]})).mark_rule(strokeDash=[4, 3], color="#8a8a85", strokeWidth=1.5).encode(x="day:T")
    st.altair_chart((lines + rule).properties(height=240), use_container_width=True)


# ---------- sidebar: feedback ----------
with st.sidebar:
    st.markdown("### Feedback")
    st.caption("👍/👎 change how much each *type* of item counts for that person "
               f"(±{config.FEEDBACK_STEP:.0%} per vote, between ×{config.FEEDBACK_BOUNDS[0]} and ×{config.FEEDBACK_BOUNDS[1]}).")
    if st.button("Reset feedback", help="Delete all 👍/👎 so the demo can be repeated"):
        reset_feedback()
        st.rerun()

# ---------- header ----------
st.title("📬 Daily Digest")
st.caption(f"{team['project']['name']} · personalized by role, subsystem phase and recent focus")

c1, c2, c3 = st.columns([2, 3, 2])
with c1:
    name = st.selectbox("Person", list(people))
with c2:
    n = st.slider("Day", 1, len(days), 8, format="Day %d")
day = days[n - 1]
person = people[name]
state = state_as_of(timeline, day)
with c3:
    since = {h["subsystem"]: h["since"] for h in state["phase_history"]}
    st.dataframe(pd.DataFrame([{"subsystem": s, "phase": p, "since": pretty_day(since[s]) if s in since else "start"}
                               for s, p in state["phases"].items()]), hide_index=True, width="stretch")

pulse = team_pulse(day, timeline, team)  # shared: nobody's feedback changes it
tab_digest, tab_compare = st.tabs(["Digest", "Compare"])

with tab_digest:
    left, right = st.columns([3, 2], gap="large")
    prefs = type_preferences(person["id"])
    with left:
        st.subheader(f"{person['name']} · {role_label(person)} · {pretty_day(day)}")
        d = build_digest(person, day, timeline, team, "none", digest_cache, prefs, pulse)
        st.write(d["intro"])
        show_pulse(d["pulse"], person, day)
        show_for_you(d, person, day)
        st.caption(f"Intro written by: {d['intro_source']}")
    with right:
        st.subheader("Focus over time")
        st.caption("Parts this person has been talking about, recent days weighted more. "
                   "This is what lets the digest follow a shift in someone's work.")
        focus_chart(person, day)
        owned = [(part, o["confidence"]) for part, owners in state["owners"].items()
                 for o in owners if o["person"] == person["id"]]
        st.caption("Owns: " + (", ".join(f"{p} ({c})" for p, c in sorted(owned, key=lambda pc: pc[1])) or "-"))
        if prefs:
            st.caption("Your feedback so far: " + "; ".join(
                f"{CATEGORY_LABEL.get(c, c).lower()} ×{v['multiplier']} (👍{v['up']} 👎{v['down']})" for c, v in prefs.items()))

    with st.expander(f"Project notebook as of {pretty_day(day)}"):
        a, b, c = st.columns(3)
        a.markdown("**Open problems**")
        for p in state["open_problems"].values():
            a.caption(f"- {p['summary']} (since {pretty_day(p['since'])})")
        a.markdown("**Unanswered questions**")
        for q in state["unanswered_questions"].values():
            a.caption(f"- {q['summary']} (asked {pretty_day(q['since'])})")
        b.markdown("**Decisions**")
        for dcs in state["decisions"]:
            b.caption(f"- {pretty_day(dcs['day'])}: {dcs['summary']}")
        c.markdown("**Owners** (declared / likely / possible)")
        for part, owners in sorted(state["owners"].items()):
            c.caption(f"- {part}: " + ", ".join(f"{names[o['person']]} ({o['confidence']})" for o in owners))
        c.markdown("**Unknown parts** (not in the catalog: add them?)")
        for u in state["unknown_parts"].values():
            c.caption(f"- “{u['written']}”, in {len(u['threads'])} thread(s), first seen {pretty_day(u['first_day'])}")

with tab_compare:
    st.caption("Same day, two people: one shared Team Pulse, different For You sections.")
    show_pulse(pulse)
    a, b = st.columns(2, gap="large")
    for col, default in ((a, 0), (b, 1)):
        with col:
            other = people[st.selectbox("Person", list(people), index=default, key=f"cmp{default}")]
            st.subheader(f"{other['name']} · {role_label(other)}")
            od = build_digest(other, day, timeline, team, "none", digest_cache, type_preferences(other["id"]), pulse)
            show_for_you(od, other, day, with_feedback=False)
