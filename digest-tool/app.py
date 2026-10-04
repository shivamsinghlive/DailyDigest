"""Streamlit UI: pick who's reading and a day, read their digest and why each item is there.

Run:  streamlit run app.py
"""
from datetime import date

import altair as alt
import pandas as pd
import streamlit as st

from digest_tool import config
from digest_tool.digest import build_digest, load_cache, pretty_day, team_pulse
from digest_tool.feedback import record_feedback, reset_feedback, type_preferences, votes_by
from digest_tool.notebook import load_notebook, state_as_of
from digest_tool.ranker import CATEGORY_LABEL, focus_scores
from digest_tool.slack_loader import clean_text, end_of_day, group_into_threads, load_messages, ts_to_dt

st.set_page_config(page_title="Daily Digest", page_icon="📬", layout="wide")

# Fixed categorical order (validated palette), with separate steps for the dark surface.
# Colors are tied to a part for the whole timeline, so changing the day never repaints a line.
SERIES_COLORS = {"light": ["#2a78d6", "#eb6834", "#1baf7a", "#eda100"],
                 "dark": ["#3987e5", "#d95926", "#199e70", "#c98500"]}
# Activity bars: one blue series, the selected day a darker step of the same ramp.
BAR_COLORS = {"light": ("#86b6ef", "#1c5cab"), "dark": ("#184f95", "#6da7ec")}

# Each kind of item gets a badge: color + icon + label, so color never carries the meaning alone.
KIND_BADGE = {
    "new_problem": ("red", "error"), "problem_update": ("red", "error"),
    "decision": ("blue", "gavel"), "phase_change": ("violet", "flag"),
    "new_question": ("gray", "help"), "question_unanswered": ("orange", "hourglass_top"),
    "question_answered": ("green", "check_circle"), "update": ("gray", "info"),
    "change_after_freeze": ("orange", "lock"),
}


@st.cache_data
def get_data():
    # The app never calls an LLM itself: it reads whatever the cache has, keywords otherwise.
    timeline, team = load_notebook("none")
    return timeline, team, load_cache(), load_messages()


timeline, team, digest_cache, messages = get_data()
days = sorted(timeline)
people = {p["id"]: p for p in team["people"]}
first = {p["id"]: p["name"].split()[0] for p in team["people"]}
full_names = {p["id"]: p["name"] for p in team["people"]}
role_label = lambda p: p["role"].replace("_", " ")


@st.cache_data
def pulse_by_day():
    return {d: team_pulse(d, timeline, team) for d in days}  # shared: nobody's feedback changes it


def digest_for(person, day):
    return build_digest(person, day, timeline, team, "none", digest_cache,
                        type_preferences(person["id"]), pulse_by_day()[day])


# ---------- pieces of a card ----------

def badges(item):
    """One line of badges: what kind of item, where it was posted, how urgent, whether to skip it."""
    change = item["change"]
    color, icon = KIND_BADGE.get(change["kind"], ("gray", "info"))
    out = [f":{color}-badge[:material/{icon}: {item['label']}]"]
    if change.get("schedule_risk"):
        out.append(":orange-badge[:material/schedule: Schedule risk]")
    if item.get("must"):
        out.append(":red-badge[:material/priority_high: Don't skip]")
    if item["urgency"] >= 4:
        out.append(f":gray-badge[Urgency {item['urgency']}/5]")
    out.append(f":gray-badge[#{item['channel']}]")
    return " ".join(out)


def conversation(thread_ts, day):
    """The Slack thread as it looked at the end of `day`, so the reader can check the summary."""
    thread = next((t for t in group_into_threads(messages, until=end_of_day(day)) if t["thread_ts"] == thread_ts), None)
    if not thread:
        return
    for m in thread["messages"]:
        when = ts_to_dt(m["ts"]).strftime("%a %b %-d, %H:%M")
        st.markdown(f"**{full_names.get(m.get('user'), m.get('user'))}** · <small>{when}</small>", unsafe_allow_html=True)
        st.markdown("> " + clean_text(m["text"], full_names).replace("\n", "\n> "))


def feedback(item, person, day):
    """👍/👎 per item. Votes count per item *type*: they change how much that type counts for this person."""
    vote = votes_by(person["id"]).get((day, item["thread_ts"]))
    label = CATEGORY_LABEL.get(item["category"], item["category"]).lower()
    if vote:
        st.caption(("👍 Marked useful" if vote > 0 else "👎 Marked not useful") + f": you'll see {'more' if vote > 0 else 'fewer'} {label}.")
        return
    choice = st.feedback("thumbs", key=f"fb:{person['id']}:{day}:{item['thread_ts']}")
    if choice is not None:
        record_feedback(person["id"], day, item["thread_ts"], item["category"], +1 if choice == 1 else -1)
        st.rerun()


def card(item, person, day, reasons, with_feedback=True):
    with st.container(border=True):
        st.markdown(badges(item))
        st.markdown(f"**{item['summary']}**")
        # The first two reasons answer "why am I seeing this"; the rest are one click away.
        st.markdown("\n".join(f"- {r}" for r in reasons[:2]) or "")
        with st.expander("All reasons and the Slack conversation"):
            for r in reasons[2:]:
                st.markdown(f"- {r}")
            conversation(item["thread_ts"], day)
        if with_feedback:
            feedback(item, person, day)


# ---------- charts and trackers ----------

def theme():
    return "dark" if st.context.theme.type == "dark" else "light"


def activity_chart(person, day):
    """Items in this person's digest per day; the selected day is darker, phase changes are marked."""
    rows = []
    for d in days:
        dd = digest_for(person, d)
        phase_moves = [c for c in timeline[d]["changes"] if c["kind"] == "phase_change"]
        rows.append({"day": pretty_day(d), "items": len(dd["pulse"]) + len(dd["for_you"]),
                     "must_read": sum(i["must"] for i in dd["for_you"]),
                     "selected": d == day, "phase": "phase change" if phase_moves else ""})
    df = pd.DataFrame(rows)
    other, selected = BAR_COLORS[theme()]
    x = alt.X("day:N", sort=None, title=None, axis=alt.Axis(labelAngle=0, labelOverlap=True, ticks=False))
    bars = alt.Chart(df).mark_bar(cornerRadiusTopLeft=4, cornerRadiusTopRight=4).encode(
        x=x, y=alt.Y("items:Q", title=None, axis=alt.Axis(tickMinStep=1, gridOpacity=0.3, domain=False)),
        color=alt.condition("datum.selected", alt.value(selected), alt.value(other)),
        tooltip=["day:N", "items:Q", alt.Tooltip("must_read:Q", title="don't skip"), alt.Tooltip("phase:N", title="note")])
    flags = alt.Chart(df[df["phase"] != ""]).mark_text(text="◆ phase", dy=-8, fontSize=11, color="#8a8a85").encode(
        x=x, y="items:Q")
    st.altair_chart((bars + flags).properties(height=150), use_container_width=True)


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
    colors = SERIES_COLORS[theme()]
    # Top 4 parts over the whole period; more lines than that stops being readable.
    top = df.groupby("part")["focus"].sum().nlargest(len(colors)).index.tolist()
    df = df[df["part"].isin(top)]
    color = alt.Color("part:N", title=None, scale=alt.Scale(domain=top, range=colors[:len(top)]),
                      legend=alt.Legend(orient="top", columns=2))
    base = alt.Chart(df).encode(
        x=alt.X("day:T", title=None, axis=alt.Axis(format="%b %d", grid=False)),
        y=alt.Y("focus:Q", title=None, axis=alt.Axis(gridOpacity=0.3, domain=False)),
        color=color, tooltip=[alt.Tooltip("day:T", format="%a %b %d"), "part:N", "focus:Q"])
    lines = base.mark_line(strokeWidth=2) + base.mark_point(size=40, filled=True)
    rule = alt.Chart(pd.DataFrame({"day": [pd.Timestamp(day)]})).mark_rule(
        strokeDash=[4, 3], color="#8a8a85", strokeWidth=1.5).encode(x="day:T")
    st.altair_chart((lines + rule).properties(height=220), use_container_width=True)


def phase_tracker(state, day):
    """One tile per subsystem: its phase, and an arrow on the day it moved."""
    since = {}
    for h in state["phase_history"]:
        since[h["subsystem"]] = h["since"]
    cols = st.columns(len(state["phases"]))
    for col, (sub, phase) in zip(cols, state["phases"].items()):
        moved_today = since.get(sub) == day
        col.metric(sub, phase, delta=f"moved to {phase} today" if moved_today else None,
                   help=f"In {phase} since {pretty_day(since[sub])}" if sub in since else "Phase at the start of the data",
                   border=True)


# ---------- sidebar: who and when ----------

if "day" not in st.session_state:
    st.session_state.day = days[min(7, len(days) - 1)]


def step(delta):
    i = days.index(st.session_state.day) + delta
    st.session_state.day = days[max(0, min(len(days) - 1, i))]


with st.sidebar:
    st.markdown("### 📬 Daily Digest")
    st.caption(team["project"]["name"])
    pid = st.radio("Reading as", list(people), format_func=lambda i: people[i]["name"],
                   captions=[role_label(p) for p in people.values()])
    st.markdown("**Day**")
    a, b = st.columns(2)
    a.button("‹ Prev", on_click=step, args=(-1,), width="stretch", disabled=st.session_state.day == days[0])
    b.button("Next ›", on_click=step, args=(1,), width="stretch", disabled=st.session_state.day == days[-1])
    st.select_slider("Day", options=days, key="day", format_func=pretty_day, label_visibility="collapsed")
    with st.expander("How to read a digest"):
        st.markdown(
            "- **Team Pulse**: the same 0-3 items for everyone (phase changes, schedule risks, big decisions).\n"
            "- **For you**: up to 5 items picked for you, each with the reasons why.\n"
            "- **Don't skip**: you were tagged, your part has a serious problem, or a question to you is waiting.\n"
            "- 👍/👎 change how much that *type* of item counts for you "
            f"(±{config.FEEDBACK_STEP:.0%} per vote, between ×{config.FEEDBACK_BOUNDS[0]} and ×{config.FEEDBACK_BOUNDS[1]}).")
    with st.expander("Demo controls"):
        if st.button("Reset all 👍/👎", help="Delete all feedback so the demo can be repeated"):
            reset_feedback()
            st.rerun()

person, day = people[pid], st.session_state.day
state = state_as_of(timeline, day)
d = digest_for(person, day)

# ---------- header ----------

st.markdown(f"## Good morning, {first[pid]} 👋")
st.caption(f"{pretty_day(day)} · {role_label(person)} · {team['project']['name']}")
st.markdown(d["intro"])

m1, m2, m3, m4 = st.columns(4)
m1.metric("Team-wide", len(d["pulse"]), help="Items everyone sees today", border=True)
m2.metric("For you", len(d["for_you"]), help="Items picked for you", border=True)
m3.metric("Don't skip", sum(i["must"] for i in d["for_you"]), help="Tagged, blocking, or your part in trouble", border=True)
new_today = sum(p["since"] == day for p in state["open_problems"].values())
m4.metric("Open problems", len(state["open_problems"]), delta=f"{new_today} new today" if new_today else None,
          delta_color="inverse", help="Across the whole project", border=True)

phase_tracker(state, day)

tab_digest, tab_team, tab_notebook = st.tabs(["📬 My digest", "👥 Team view", "📓 Project notebook"])

# ---------- my digest ----------

with tab_digest:
    left, right = st.columns([3, 2], gap="large")
    with left:
        st.markdown("#### 🧭 Team Pulse")
        st.caption("The same items for everyone, so the whole team works from one picture.")
        if not d["pulse"]:
            st.info("Nothing team-wide today.", icon=":material/check:")
        for item in d["pulse"]:
            card(item, person, day, item["reasons"] + [f"For you: {r}" for r in item.get("for_you", [])])
        st.markdown("#### 👤 For you")
        if not d["for_you"]:
            st.info("Nothing else for you today.", icon=":material/check:")
        for item in d["for_you"]:
            card(item, person, day, item["reasons"])
        st.caption(f"Intro: {d['intro_source']}")
    with right:
        st.markdown("#### Your last two weeks")
        st.caption("Items in your digest each day. Hover a bar for details.")
        activity_chart(person, day)
        st.markdown("#### What you've been working on")
        st.caption("Parts you've talked about, recent days weighted more. The digest follows this as your work shifts.")
        focus_chart(person, day)
        owned = sorted((o["confidence"], part) for part, owners in state["owners"].items()
                       for o in owners if o["person"] == pid)
        st.markdown("#### Parts you own")
        st.markdown(" ".join(f":{'blue' if c == 'declared' else 'gray'}-badge[{part} · {c}]" for c, part in owned) or "None yet.")
        prefs = type_preferences(pid)
        if prefs:
            st.markdown("#### Your feedback so far")
            st.markdown("\n".join(f"- {CATEGORY_LABEL.get(c, c)}: ×{v['multiplier']} (👍{v['up']} 👎{v['down']})"
                                  for c, v in prefs.items()))

# ---------- team view: who got what ----------

with tab_team:
    st.caption(f"Everyone's digest for {pretty_day(day)}. Same Team Pulse for all; the rest is personal.")
    rows = {}
    for p in people.values():
        pd_ = digest_for(p, day)
        for item in pd_["pulse"]:
            rows.setdefault(item["thread_ts"], {"Item": f"{item['label']}: {item['summary']}"})[first[p["id"]]] = "🧭 team"
        for item in pd_["for_you"]:
            rows.setdefault(item["thread_ts"], {"Item": f"{item['label']}: {item['summary']}"})[first[p["id"]]] = (
                "⚑ don't skip" if item["must"] else "● for you")
    if rows:
        table = pd.DataFrame(list(rows.values())).reindex(columns=["Item"] + [first[i] for i in people]).fillna("")
        st.table(table.set_index("Item"))  # a static table wraps long summaries; a dataframe would cut them
        st.caption("🧭 in the Team Pulse · ● in For you · ⚑ in For you and not to be skipped · blank: not shown "
                   "(not relevant, or they already replied in that thread)")
    else:
        st.info("A quiet day: nobody got anything.", icon=":material/check:")

# ---------- project notebook ----------

with tab_notebook:
    st.caption(f"What the tool knows about the project at the end of {pretty_day(day)}, built from Slack.")
    today = date.fromisoformat(day)
    a, b = st.columns(2, gap="large")
    with a:
        st.markdown(f"#### 🔴 Open problems ({len(state['open_problems'])})")
        st.markdown("\n".join(
            f"- {p['summary']} :gray-badge[open {(today - date.fromisoformat(p['since'])).days} d] :gray-badge[#{p['channel_name']}]"
            for p in sorted(state["open_problems"].values(), key=lambda p: p["since"])) or "None.")
        st.markdown(f"#### ✅ Closed problems ({len(state['closed_problems'])})")
        st.markdown("\n".join(
            f"- ~~{p['summary']}~~ :green-badge[closed {pretty_day(p['closed_day'])}] "
            + (f"by a decision/fix in #{p['closed_in']} about the {', '.join(p['via_parts'])}" if p["via_parts"]
               else "in its own thread")
            for p in state["closed_problems"]) or "None yet.")
        st.markdown("#### ⏳ Unanswered questions")
        waiting = [q for q in state["unanswered_questions"].values() if q["flagged"]]
        st.markdown("\n".join(f"- {q['summary']} (asked {pretty_day(q['since'])})" for q in waiting) or "None over 48h.")
    with b:
        st.markdown("#### 🔒 Changes after design freeze")
        freeze = [c for dd in days if dd <= day for c in timeline[dd]["changes"] if c["kind"] == "change_after_freeze"]
        st.markdown("\n".join(f"- {pretty_day(c['day'])}: {c['summary']}" for c in freeze) or "None.")
        st.markdown("#### 🧑‍⚖️ Decisions")
        st.markdown("\n".join(f"- {pretty_day(x['day'])}: {x['summary']}" for x in reversed(state["decisions"])) or "None yet.")
        st.markdown("#### 🔧 Who owns what")
        st.caption("declared = on the team list · likely / possible = inferred from who discusses and answers about it")
        st.dataframe(pd.DataFrame([{"Part": part, "Owners": ", ".join(f"{first[o['person']]} ({o['confidence']})" for o in owners)}
                                   for part, owners in sorted(state["owners"].items())]), hide_index=True, width="stretch")
        if state["unknown_parts"]:
            with st.expander(f"❓ Parts mentioned but not in the catalog ({len(state['unknown_parts'])}): add them?"):
                    st.markdown("\n".join(f"- “{u['written']}”, in {len(u['threads'])} thread(s), first seen {pretty_day(u['first_day'])}"
                                      for u in state["unknown_parts"].values()))
