"""Streamlit UI: pick who's reading and a day, read their digest and why each item is there.

Run:  streamlit run app.py
"""
import html
import re
from datetime import date

import altair as alt
import pandas as pd
import streamlit as st

from digest_tool import config
from digest_tool.digest import build_digest, load_cache, pretty_day, team_pulse
from digest_tool.feedback import record_feedback, reset_feedback, type_preferences, votes_by
from digest_tool.memory import current_state, history
from digest_tool.notebook import PHASES, load_notebook, state_as_of
from digest_tool.ranker import CATEGORY_LABEL, fact_label, focus_scores
from digest_tool.slack_loader import clean_text, end_of_day, group_into_threads, load_messages, ts_to_dt

st.set_page_config(page_title="Daily Digest", page_icon="📬", layout="wide")

# Fixed categorical order (validated palette), with separate steps for the dark surface.
# Colors are tied to a part for the whole timeline, so changing the day never repaints a line.
SERIES_COLORS = {"light": ["#2a78d6", "#eb6834", "#1baf7a", "#eda100"],
                 "dark": ["#3987e5", "#d95926", "#199e70", "#c98500"]}
# Activity bars: one blue series, the selected day a darker step of the same ramp.
BAR_COLORS = {"light": ("#86b6ef", "#1c5cab"), "dark": ("#184f95", "#6da7ec")}

# Each kind of item: badge color, icon, and the color of the card's left edge. Badges always carry a
# label, so color never carries the meaning alone. Edge colors are mid-tones that read in light and dark.
KIND_STYLE = {
    "new_problem": ("red", "error", "problem"), "problem_update": ("red", "error", "problem"),
    "decision": ("blue", "gavel", "decision"), "phase_change": ("violet", "flag", "phase"),
    "new_question": ("orange", "help", "question"), "question_unanswered": ("orange", "hourglass_top", "question"),
    "question_answered": ("green", "check_circle", "resolved"), "update": ("gray", "info", "update"),
    "change_after_freeze": ("orange", "lock", "freeze"),
}
# How the item moved the project's memory (digest.what_changed). Some override the card edge.
DELTA_STYLE = {
    "NEW": ("blue", "fiber_new", "New", None), "UPDATED": ("violet", "update", "Updated", None),
    "RESOLVED": ("green", "task_alt", "Resolved", "resolved"), "REOPENED": ("orange", "replay", "Reopened", "freeze"),
    "CONFLICT": ("red", "report", "Needs clarification", "problem"),
}
ROLE_ICON = {"mechanical_engineer": "🔧", "electrical_engineer": "⚡", "supply_chain": "📦",
             "engineering_manager": "🧭", "product_manager": "🎯"}

STYLE = """
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap');
/* Inter for text. Icons set their own font on their own element, so they are unaffected. */
.stApp, .stApp p, .stApp li, .stApp label, .stApp button, .stApp input, .stApp h1, .stApp h2, .stApp h3,
.stApp h4, .stApp [data-testid="stCaptionContainer"], .stApp [data-testid="stMarkdownContainer"] {
  font-family: 'Inter', 'Source Sans Pro', sans-serif; }
.stApp h2 { letter-spacing: -0.02em; font-weight: 700; }
/* Avatars: initials on a fixed color per person. */
.dd-avatar { display: inline-flex; align-items: center; justify-content: center; width: 1.6rem; height: 1.6rem;
             border-radius: 50%; color: #fff; font-size: 0.68rem; font-weight: 700; margin-right: 0.45rem;
             vertical-align: middle; letter-spacing: 0.02em; }
.dd-meta { font-size: 0.86rem; opacity: 0.85; margin-bottom: 0.2rem; }
.dd-meta .dd-dim { opacity: 0.7; }
/* Today at a glance: three big numbers in the greeting block. */
.dd-kpis { display: flex; gap: 0.6rem; margin-top: 0.4rem; }
.dd-kpi { flex: 1; min-width: 0; padding: 0.7rem 0.9rem; border-radius: 0.8rem; background: rgba(127, 127, 127, 0.10); }
.dd-kpi b { display: block; font-size: 2rem; line-height: 1.1; font-weight: 700; }
.dd-kpi span { font-size: 0.8rem; opacity: 0.75; }
/* Phase track: each subsystem's way through Concept → Production. */
.dd-phases { display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: 0.7rem; margin: 0.3rem 0 0.4rem; }
.dd-phase { padding: 0.55rem 0.75rem; border-radius: 0.7rem; border: 1px solid rgba(127, 127, 127, 0.22); }
.dd-phase .dd-sub { font-size: 0.8rem; opacity: 0.75; text-transform: capitalize; }
.dd-phase .dd-now { font-weight: 700; font-size: 1.05rem; }
.dd-phase .dd-moved { color: #1baf7a; font-size: 0.75rem; font-weight: 600; margin-left: 0.3rem; }
.dd-track { display: flex; gap: 3px; margin-top: 0.35rem; }
.dd-track i { flex: 1; height: 5px; border-radius: 3px; background: rgba(127, 127, 127, 0.25); }
.dd-track i.done { background: #9d93ea; }
.dd-track i.now { background: #2a78d6; }
.dd-problems { color: #d03b3b; font-weight: 600; font-size: 0.9rem; }
/* Section headings with a count bubble. */
.dd-h { font-size: 1.25rem; font-weight: 700; margin: 0.6rem 0 0.1rem; }
.dd-count { display: inline-block; min-width: 1.5rem; padding: 0 0.45rem; margin-left: 0.4rem; border-radius: 999px;
            background: rgba(42, 120, 214, 0.15); color: #2a78d6; font-size: 0.85rem; text-align: center; vertical-align: middle; }
/* Digest cards: a colored left edge says what kind of item it is, before you read a word. */
[class*="st-key-card-"] { border-left: 5px solid #9a9a94 !important; }
[class*="st-key-card-problem"]  { border-left-color: #d03b3b !important; }
[class*="st-key-card-decision"] { border-left-color: #2a78d6 !important; }
[class*="st-key-card-phase"]    { border-left-color: #7d6fe0 !important; }
[class*="st-key-card-question"] { border-left-color: #e0a100 !important; }
[class*="st-key-card-freeze"]   { border-left-color: #e8833a !important; }
[class*="st-key-card-resolved"] { border-left-color: #1baf7a !important; }
/* "What changed": a tinted box, so memory facts stand apart from the reasons. */
[class*="st-key-changed-"] { background: rgba(42, 120, 214, 0.10); border-radius: 0.5rem; padding: 0.6rem 0.85rem 0.8rem; }
[class*="st-key-changed-"] p { margin-bottom: 0.15rem; }
/* Team Pulse cards: a faint tint, so shared items read differently from personal ones. */
[class*="st-key-card-"][class*="-pulse-"] { background: rgba(42, 120, 214, 0.035); }
[class*="st-key-card-"] { transition: box-shadow 0.15s ease; }
[class*="st-key-card-"]:hover { box-shadow: 0 6px 18px rgba(0, 0, 0, 0.10); }
.dd-summary { font-weight: 600; font-size: 1.04rem; line-height: 1.45; margin: 0.1rem 0 0.4rem; }
/* One blue accent instead of Streamlit's red, in light and dark mode alike. (A theme color in
   config.toml would do this too, but it switches off the viewer's dark mode.) */
button[data-baseweb="tab"][aria-selected="true"] p { color: #2a78d6 !important; }
[data-baseweb="tab-highlight"] { background-color: #2a78d6 !important; }
label[data-baseweb="radio"]:has(input:checked) > div:first-child { background-color: #2a78d6 !important; }
label[data-baseweb="checkbox"]:has(input:checked) > div:first-child { background-color: #2a78d6 !important; }
[data-testid="stSlider"] [data-baseweb="slider"] { filter: hue-rotate(212deg); }
button[kind="segmented_controlActive"] { color: #2a78d6 !important; border-color: #2a78d6 !important;
                                         background-color: rgba(42, 120, 214, 0.12) !important; z-index: 1; }
/* The day timeline: compact chips, so two weeks fit on one line. */
.st-key-timeline button { padding: 3px 7px !important; }
.st-key-timeline button p { font-size: 0.82rem !important; }
/* Sidebar: a touch smaller, so labels stay on one line. */
section[data-testid="stSidebar"] p, section[data-testid="stSidebar"] label { font-size: 0.92rem; }
/* The greeting block. */
.st-key-hero { background: linear-gradient(135deg, rgba(42,120,214,0.10), rgba(125,111,224,0.06));
               border-radius: 0.9rem; padding: 1.1rem 1.4rem 0.6rem; }
.st-key-hero h2 { padding-top: 0; }
</style>
"""


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
# One fixed avatar color per person (categorical palette order), white initials on top.
AVATAR_COLORS = ["#2a78d6", "#eb6834", "#1baf7a", "#7d6fe0", "#e87ba4", "#008300", "#e34948", "#c98500"]
avatar_color = {pid: AVATAR_COLORS[i % len(AVATAR_COLORS)] for i, pid in enumerate(people)}


def avatar(pid):
    initials = "".join(w[0] for w in full_names.get(pid, "?").split()[:2]).upper()
    return f"<span class='dd-avatar' style='background:{avatar_color.get(pid, '#8a8a85')}'>{initials}</span>"


def heading(text, count=None):
    bubble = f"<span class='dd-count'>{count}</span>" if count is not None else ""
    st.markdown(f"<div class='dd-h'>{text}{bubble}</div>", unsafe_allow_html=True)


@st.cache_data
def pulse_by_day():
    return {d: team_pulse(d, timeline, team) for d in days}  # shared: nobody's feedback changes it


def digest_for(person, day, since=None):
    pulse = pulse_by_day()[day] if since is None else team_pulse(day, timeline, team, since)
    return build_digest(person, day, timeline, team, "none", digest_cache,
                        type_preferences(person["id"]), pulse, since=since)


# ---------- a digest card ----------

def card_badges(item):
    """At most four badges: how it changed, what it is, schedule risk, don't skip."""
    change = item["change"]
    color, icon, _ = KIND_STYLE.get(change["kind"], ("gray", "info", "update"))
    out = []
    if item.get("delta_badge"):
        dcolor, dicon, dlabel, _ = DELTA_STYLE[item["delta_badge"]]
        out.append(f":{dcolor}-badge[:material/{dicon}: {dlabel}]")
    out.append(f":{color}-badge[:material/{icon}: {item['label']}]")
    if change.get("schedule_risk"):
        out.append(":orange-badge[:material/schedule: Schedule risk]")
    if item.get("must"):
        out.append(":red-badge[:material/priority_high: Don't skip]")
    return " ".join(out)


def card_tone(item):
    tone = KIND_STYLE.get(item["change"]["kind"], ("gray", "info", "update"))[2]
    return (DELTA_STYLE.get(item.get("delta_badge"), (None,) * 4)[3]) or tone


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
        st.caption(("👍" if vote > 0 else "👎") + f" you'll see {'more' if vote > 0 else 'fewer'} {label}")
        return
    choice = st.feedback("thumbs", key=f"fb:{person['id']}:{day}:{item['thread_ts']}")
    if choice is not None:
        record_feedback(person["id"], day, item["thread_ts"], item["category"], +1 if choice == 1 else -1)
        st.rerun()


def card(item, person, day, reasons, section, with_feedback=True):
    uid = f"{section}-{re.sub(r'[^0-9a-z]', '_', item['thread_ts'])}"
    reasons = list(dict.fromkeys(reasons))  # a pulse item's personal and team reasons can overlap
    with st.container(border=True, key=f"card-{card_tone(item)}-{uid}"):
        author = item["change"].get("root_author")
        started = ts_to_dt(item["thread_ts"]).strftime("%a %-d %b, %H:%M")
        st.markdown(f"<div class='dd-meta'>{avatar(author)}<b>{html.escape(full_names.get(author, 'Someone'))}</b> "
                    f"<span class='dd-dim'>in #{html.escape(item['channel'])} · {started}</span></div>", unsafe_allow_html=True)
        st.markdown(card_badges(item))
        st.markdown(f"<div class='dd-summary'>{html.escape(item['summary'])}</div>", unsafe_allow_html=True)
        if item.get("what_changed"):  # from project memory: before → after, linked issues, constraints in force
            with st.container(key=f"changed-{uid}"):
                st.markdown("  \n".join(f":material/arrow_right_alt: {line}" for line in item["what_changed"]))
        if reasons:
            st.caption("**Why you're seeing this:** " + " · ".join(reasons[:2]))
        meta, thumbs = st.columns([5, 1], vertical_alignment="center")
        meta.caption(f"Urgency {item['urgency']}/5")
        if with_feedback:
            with thumbs:
                feedback(item, person, day)
        with st.expander("Conversation and all reasons"):
            for r in reasons[2:]:
                st.markdown(f"- {r}")
            conversation(item["thread_ts"], day)


# ---------- charts ----------

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
    st.altair_chart((bars + flags).properties(height=140), use_container_width=True)


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
    st.altair_chart((lines + rule).properties(height=200), use_container_width=True)


def project_status(state, day):
    """A tile per subsystem with its way through Concept → Production, and the open-problem count."""
    since = {h["subsystem"]: h["since"] for h in state["phase_history"]}
    tiles = []
    for sub, phase in state["phases"].items():
        n = PHASES.index(phase)
        track = "".join(f"<i class='{'now' if i == n else 'done' if i < n else ''}' title='{p}'></i>" for i, p in enumerate(PHASES))
        moved = "<span class='dd-moved'>↑ today</span>" if since.get(sub) == day else ""
        tiles.append(f"<div class='dd-phase'><div class='dd-sub'>{html.escape(sub)}</div>"
                     f"<div class='dd-now'>{phase}{moved}</div><div class='dd-track'>{track}</div></div>")
    open_now = len(state["open_problems"])
    new_today = sum(p["since"] == day for p in state["open_problems"].values())
    tiles.append(f"<div class='dd-phase'><div class='dd-sub'>open problems</div>"
                 f"<div class='dd-now dd-problems'>{open_now}{f'<span class=dd-moved style=color:#d03b3b>+{new_today} today</span>' if new_today else ''}</div>"
                 f"<div class='dd-track'>{''.join('<i class=now style=background:#d03b3b></i>' for _ in range(min(open_now, 5)))}</div></div>")
    st.markdown(f"<div class='dd-phases'>{''.join(tiles)}</div>", unsafe_allow_html=True)


# ---------- sidebar: who and when ----------

if st.session_state.get("day") is None:  # first visit, or the selected day chip was clicked off
    st.session_state.day = st.session_state.get("last_day", days[min(7, len(days) - 1)])
st.session_state.last_day = st.session_state.day


def step(delta):
    i = days.index(st.session_state.day) + delta
    st.session_state.day = days[max(0, min(len(days) - 1, i))]


with st.sidebar:
    st.markdown("## 📬 Daily Digest")
    st.caption(team["project"]["name"])
    pid = st.radio("Who's reading?", list(people), format_func=lambda i: f"{ROLE_ICON.get(people[i]['role'], '👤')} {people[i]['name']}",
                   captions=[role_label(p) for p in people.values()])
    earlier = days[:days.index(st.session_state.day)]
    since = None
    if earlier and st.toggle("Catch up since an earlier day", help="E.g. on Monday: everything since Friday, "
                                                                   "one item per thread"):
        since = st.selectbox("Changes since", options=earlier[::-1], index=min(2, len(earlier) - 1),
                             format_func=pretty_day)
    st.divider()
    with st.expander("How to read this"):
        st.markdown(
            "- **Team Pulse**: the same 0-3 items for everyone (phase changes, schedule risks, big decisions).\n"
            "- **For you**: up to 5 items picked for you, each with the reasons why.\n"
            "- The **colored edge** says what kind of item it is: red problem, blue decision, violet phase change, "
            "amber question, orange freeze issue or reopened, green resolved.\n"
            "- **What changed** (blue box) comes from project memory: before → after, the issue it belongs to, "
            "constraints still in force.\n"
            "- **Don't skip**: you were tagged, your part has a serious problem, or a question to you is waiting.\n"
            "- 👍/👎 change how much that *type* of item counts for you "
            f"(±{config.FEEDBACK_STEP:.0%} per vote, between ×{config.FEEDBACK_BOUNDS[0]} and ×{config.FEEDBACK_BOUNDS[1]}).")
    with st.expander("Demo controls"):
        if st.button("Reset all 👍/👎", help="Delete all feedback so the demo can be repeated"):
            reset_feedback()
            st.rerun()

person, day = people[pid], st.session_state.day
state = state_as_of(timeline, day)
d = digest_for(person, day, since)
st.html(STYLE)

# ---------- header ----------

with st.container(key="hero"):
    words, numbers = st.columns([3, 2], vertical_alignment="center")
    with words:
        st.markdown(f"<h2>{avatar(pid)} Good morning, {html.escape(first[pid])} 👋</h2>", unsafe_allow_html=True)
        st.caption(f"{pretty_day(day)}{f' · catching up since {pretty_day(since)}' if since else ''} · "
                   f"{role_label(person)} · {team['project']['name']}")
        st.markdown(d["intro"])
    must = sum(i["must"] for i in d["for_you"])
    kpi = lambda n, label, color: f"<div class='dd-kpi'><b style='color:{color}'>{n}</b><span>{label}</span></div>"
    numbers.markdown("<div class='dd-kpis'>" + kpi(len(d["pulse"]), "team-wide", "#2a78d6")
                     + kpi(len(d["for_you"]), "for you", "#7d6fe0")
                     + kpi(must, "don't skip", "#d03b3b" if must else "#8a8a85") + "</div>", unsafe_allow_html=True)
project_status(state, day)

# The whole period as a row of day chips; ◆ marks a day a subsystem changed phase.
phase_days = {d for d in days for c in timeline[d]["changes"] if c["kind"] == "phase_change"}
prev_col, strip, next_col = st.columns([1, 16, 1], vertical_alignment="center")
prev_col.button("‹", on_click=step, args=(-1,), disabled=day == days[0], help="Previous day", key="prev")
with strip.container(key="timeline"):
    st.segmented_control("Day", options=days, key="day", label_visibility="collapsed",
                         format_func=lambda x: f"{date.fromisoformat(x):%a %-d}{' ◆' if x in phase_days else ''}")
next_col.button("›", on_click=step, args=(1,), disabled=day == days[-1], help="Next day", key="next")

tab_digest, tab_team, tab_notebook = st.tabs(["📬 My digest", "👥 Team view", "📓 Project notebook"])

# ---------- my digest ----------

with tab_digest:
    left, right = st.columns([3, 2], gap="large")
    with left:
        heading("🧭 Team Pulse", len(d["pulse"]))
        st.caption("The same items for everyone, so the whole team works from one picture.")
        if not d["pulse"]:
            st.info("A quiet day for the team: nothing everyone needs to know.", icon=":material/check:")
        for item in d["pulse"]:
            card(item, person, day, item.get("for_you", []) + item["reasons"], "pulse")  # personal reasons first
        heading("👤 For you", len(d["for_you"]))
        if not d["for_you"]:
            st.success("You're all caught up 🎉 Nothing else needs your attention.", icon=":material/done_all:")
        for item in d["for_you"]:
            card(item, person, day, item["reasons"], "mine")
        st.caption("✨ Intro written by Claude Haiku, checked against the items" if "anthropic" in d["intro_source"]
                   else "Intro from a template")
    with right:
        with st.container(border=True):
            st.markdown("**Your last two weeks**")
            st.caption("Items in your digest each day. Hover a bar for details.")
            activity_chart(person, day)
        with st.container(border=True):
            st.markdown("**What you've been working on**")
            st.caption("Parts you've talked about, recent days weighted more. The digest follows this as your work shifts.")
            focus_chart(person, day)
        with st.container(border=True):
            owned = sorted((o["confidence"], part) for part, owners in state["owners"].items()
                           for o in owners if o["person"] == pid)
            st.markdown("**Parts you own**")
            st.markdown(" ".join(f":{'blue' if c == 'declared' else 'gray'}-badge[{part} · {c}]" for c, part in owned)
                        or "None yet.")
            prefs = type_preferences(pid)
            if prefs:
                st.markdown("**Your feedback so far**")
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
    st.caption(f"What the tool knows about the project at the end of {pretty_day(day)}, built from Slack. "
               "Nothing is overwritten: earlier values and closed problems stay here.")
    today = date.fromisoformat(day)
    mem = state["memory"]
    n_open, n_facts = len(state["open_problems"]), len(current_state(mem, as_of=day, types={"FACT", "CONSTRAINT"}))
    t_problems, t_facts, t_decisions, t_owners, t_log = st.tabs([
        f"🔴 Problems ({n_open} open)", f"📐 Facts & constraints ({n_facts})", "🧑‍⚖️ Decisions & freeze",
        "🔧 Owners", "🕘 Memory log"])

    with t_problems:
        a, b = st.columns(2, gap="large")
        with a:
            st.markdown(f"**Open ({n_open})**")
            for p in sorted(state["open_problems"].values(), key=lambda p: p["since"]):
                with st.container(border=True, key=f"card-problem-open-{re.sub(r'[^0-9a-z]', '_', p['thread_ts'])}"):
                    st.markdown(p["summary"])
                    age = (today - date.fromisoformat(p["since"])).days
                    st.caption(f"{'new today' if age == 0 else f'open {age} day' + ('s' if age > 1 else '')} · #{p['channel_name']}")
            if not state["open_problems"]:
                st.success("No open problems.", icon=":material/done_all:")
            waiting = [q for q in state["unanswered_questions"].values() if q["flagged"]]
            if waiting:
                st.markdown(f"**Waiting for an answer over 48h ({len(waiting)})**")
                for q in waiting:
                    st.markdown(f"- {q['summary']} :gray-badge[asked {pretty_day(q['since'])}]")
        with b:
            st.markdown(f"**Closed ({len(state['closed_problems'])})**")
            for p in reversed(state["closed_problems"]):
                with st.container(border=True, key=f"card-resolved-{re.sub(r'[^0-9a-z]', '_', p['thread_ts'])}"):
                    st.markdown(f"~~{p['summary']}~~")
                    how = (f"by a decision or fix in #{p['closed_in']} about the {', '.join(p['via_parts'])}"
                           if p["via_parts"] else "in its own thread")
                    st.caption(f"closed {pretty_day(p['closed_day'])} {how}")

    with t_facts:
        st.caption("Current values, with every earlier value kept. A disagreement without the authority to change "
                   "a value is flagged, not written over it.")
        changed = {e["key"] for e in mem["events"] if e["change"] == "UPDATED" and e["day"] <= day}
        facts = sorted(current_state(mem, as_of=day, types={"FACT", "CONSTRAINT"}),
                       key=lambda m: (not m["conflicts"], m["key"] not in changed, m["type"] != "CONSTRAINT", m["key"]))
        rows = []
        for m in facts:
            trail = [e["after"] for e in history(mem, m["key"]) if e["change"] in ("NEW", "UPDATED") and e["day"] <= day]
            disputes = [f"{c['value']} ({c['note']})" for c in m["conflicts"] if c["day"] <= day]
            rows.append({"": "📌" if m["type"] == "CONSTRAINT" else "📏", "What": fact_label(m["key"]), "Now": str(m["value"]),
                         "Before": " → ".join(map(str, trail[:-1])), "Since": pretty_day(m["valid_from"]),
                         "Needs clarification": "⚠ " + "; ".join(disputes) if disputes else ""})
        if rows:
            st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch", height=min(36 * len(rows) + 38, 560),
                         column_config={"": st.column_config.TextColumn(width=30), "What": st.column_config.TextColumn(width="medium"),
                                        "Now": st.column_config.TextColumn(width="small")})
            st.caption("📌 constraint (a target, limit or deadline) · 📏 fact (a measured or current value). "
                       "Disputed and changed values first.")
        else:
            st.info("No facts with values yet.")

    with t_decisions:
        a, b = st.columns(2, gap="large")
        with a:
            st.markdown("**Decisions**")
            for x in reversed(state["decisions"]):
                with st.container(border=True, key=f"card-decision-{re.sub(r'[^0-9a-z]', '_', x['thread_ts'])}"):
                    st.markdown(x["summary"])
                    st.caption(pretty_day(x["day"]))
            if not state["decisions"]:
                st.caption("None yet.")
        with b:
            st.markdown("**Changes after design freeze**")
            freeze = [c for dd in days if dd <= day for c in timeline[dd]["changes"] if c["kind"] == "change_after_freeze"]
            for c in freeze:
                with st.container(border=True, key=f"card-freeze-{re.sub(r'[^0-9a-z]', '_', c['thread_ts'])}"):
                    st.markdown(c["summary"])
                    st.caption(f"{pretty_day(c['day'])} · {', '.join(c['frozen_parts'])} · no ECO mentioned")
            if not freeze:
                st.caption("None.")

    with t_owners:
        st.caption("declared = on the team list · likely / possible = inferred from who discusses and answers about it")
        st.dataframe(pd.DataFrame([{"Part": part, "Owners": ", ".join(f"{first[o['person']]} ({o['confidence']})" for o in owners)}
                                   for part, owners in sorted(state["owners"].items())]), hide_index=True, width="stretch")
        if state["unknown_parts"]:
            with st.expander(f"❓ Parts mentioned but not in the catalog ({len(state['unknown_parts'])}): add them?"):
                st.markdown("\n".join(f"- “{u['written']}”, in {len(u['threads'])} thread(s), first seen {pretty_day(u['first_day'])}"
                                      for u in state["unknown_parts"].values()))

    with t_log:
        st.caption(f"Every change the project's memory recorded on {pretty_day(day)}. Events are never edited.")
        log = [e for e in mem["events"] if e["day"] == day]
        if log:
            st.dataframe(pd.DataFrame([{"Change": e["change"], "About": e["key"].split(":", 1)[-1],
                                        "Before": "" if e["before"] is None else str(e["before"]),
                                        "After": "" if e["after"] is None else str(e["after"]), "Note": e["note"]}
                                       for e in log]), hide_index=True, width="stretch")
        else:
            st.info("Nothing changed in memory on this day.")
