"""End-to-end checks for the two-session trace comparison feature.

The comparison replays each named session under its own stored protocol and
compares the ordered (action, message) events split into the A->B and B->A
directions, ignoring transition ids, endpoint states and the interleaving
between the two directions.
"""

import threading

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)

failures = []


def check(name, cond, detail=""):
    status = "PASS" if cond else "FAIL"
    print(f"[{status}] {name}" + (f" :: {detail}" if detail and not cond else ""))
    if not cond:
        failures.append(name)


# Classic interleavable two-party exchange with room for a second round:
#   A sends m (a1, A 0->1), then receives n (a2, A 1->2)
#   B sends n (b2, B 0->1), then receives m (b1, B 1->2)
# The two first-round sends are independent from the initial configuration, so
# e.g. [a1, b2, b1, a2] and [b2, a1, a2, b1] are both valid traces with the same
# per-direction events but a different cross-direction interleaving.
# b3/a3 are an alternative first B->A exchange carrying p instead of n; from
# A=2/B=2 a second B->A round can carry either q (b4/a4) or r (b6/a6), which
# diverges only at the third per-direction event after a shared [send n,
# receive n] prefix.
P = {
    "A": {"states": [0, 1, 2, 3], "initial": 0, "finals": [3]},
    "B": {"states": [0, 1, 2, 3], "initial": 0, "finals": [3]},
    "transitions": [
        {"id": "a1", "from": "A", "to": "B", "action": "send", "message": "m",
         "source": 0, "target": 1},
        {"id": "a2", "from": "B", "to": "A", "action": "receive", "message": "n",
         "source": 1, "target": 2},
        {"id": "b1", "from": "A", "to": "B", "action": "receive", "message": "m",
         "source": 1, "target": 2},
        {"id": "b2", "from": "B", "to": "A", "action": "send", "message": "n",
         "source": 0, "target": 1},
        {"id": "b3", "from": "B", "to": "A", "action": "send", "message": "p",
         "source": 0, "target": 1},
        {"id": "a3", "from": "B", "to": "A", "action": "receive", "message": "p",
         "source": 1, "target": 2},
        {"id": "b4", "from": "B", "to": "A", "action": "send", "message": "q",
         "source": 2, "target": 3},
        {"id": "a4", "from": "B", "to": "A", "action": "receive", "message": "q",
         "source": 2, "target": 3},
        {"id": "b6", "from": "B", "to": "A", "action": "send", "message": "r",
         "source": 2, "target": 3},
        {"id": "a6", "from": "B", "to": "A", "action": "receive", "message": "r",
         "source": 2, "target": 3},
    ],
}

# Same ids/shape as P but the B->A message n is renamed to ZZ: replaying under a
# different protocol is allowed and surfaces as a message divergence.
PRENAMED = {
    **P,
    "transitions": [
        {"id": "a1", "from": "A", "to": "B", "action": "send", "message": "m",
         "source": 0, "target": 1},
        {"id": "a2", "from": "B", "to": "A", "action": "receive", "message": "ZZ",
         "source": 1, "target": 2},
        {"id": "b1", "from": "A", "to": "B", "action": "receive", "message": "m",
         "source": 1, "target": 2},
        {"id": "b2", "from": "B", "to": "A", "action": "send", "message": "ZZ",
         "source": 0, "target": 1},
        {"id": "b3", "from": "B", "to": "A", "action": "send", "message": "p",
         "source": 0, "target": 1},
        {"id": "a3", "from": "B", "to": "A", "action": "receive", "message": "p",
         "source": 1, "target": 2},
        {"id": "b4", "from": "B", "to": "A", "action": "send", "message": "q",
         "source": 2, "target": 3},
        {"id": "a4", "from": "B", "to": "A", "action": "receive", "message": "q",
         "source": 2, "target": 3},
        {"id": "b6", "from": "B", "to": "A", "action": "send", "message": "r",
         "source": 2, "target": 3},
        {"id": "a6", "from": "B", "to": "A", "action": "receive", "message": "r",
         "source": 2, "target": 3},
    ],
}

# Same (action, message) events as P's first exchange, but the final transitions
# land in state 3 instead of 2: endpoint states differ yet the comparison must
# stay equal (and the trace ends in the completion configuration here).
PRESHAPED = {
    "A": {"states": [0, 1, 3], "initial": 0, "finals": [3]},
    "B": {"states": [0, 1, 3], "initial": 0, "finals": [3]},
    "transitions": [
        {"id": "a1", "from": "A", "to": "B", "action": "send", "message": "m",
         "source": 0, "target": 1},
        {"id": "a2", "from": "B", "to": "A", "action": "receive", "message": "n",
         "source": 1, "target": 3},
        {"id": "b1", "from": "A", "to": "B", "action": "receive", "message": "m",
         "source": 1, "target": 3},
        {"id": "b2", "from": "B", "to": "A", "action": "send", "message": "n",
         "source": 0, "target": 1},
    ],
}


def new_session(protocol=P):
    return client.post("/v1/sessions", json=protocol).json()["id"]


def run(sid, tid, ver):
    return client.post(f"/v1/sessions/{sid}/transitions",
                       json={"transition_id": tid, "expected_version": ver})


def steps(session_id, ids):
    for index, tid in enumerate(ids):
        r = run(session_id, tid, index)
        assert r.status_code == 200, (tid, r.status_code, r.text)


def compare(left, right, lv, rv):
    return client.post("/v1/trace-comparisons", json={
        "left_session_id": left,
        "right_session_id": right,
        "left_expected_version": lv,
        "right_expected_version": rv,
    })


# per-direction events of the first exchange, for documentation:
#   [a1, b2, b1, a2] -> A_to_B: send m, receive m ; B_to_A: send n, receive n
#   [b2, a1, a2, b1] -> same events per direction, opposite cross interleaving
FULL_AB = ["a1", "b2", "b1", "a2"]
FULL_BA = ["b2", "a1", "a2", "b1"]
FULL_MIX = ["b2", "a1", "b1", "a2"]
# left continues the B->A round with q, right with r after the shared prefix
ROUND_Q = ["b4", "a4"]
ROUND_R = ["b6", "a6"]

# 1. interleaving-only difference -> equal in both directions.
s_ab = new_session()
s_ba = new_session()
steps(s_ab, FULL_AB)
steps(s_ba, FULL_BA)
r = compare(s_ab, s_ba, 4, 4)
check("interleave only 200", r.status_code == 200, (r.status_code, r.text))
b = r.json()
check("echo session ids", b["left_session_id"] == s_ab and b["right_session_id"] == s_ba)
check("echo versions", b["left_version"] == 4 and b["right_version"] == 4)
for direction in ("A_to_B", "B_to_A"):
    d = b["directions"][direction]
    check(f"{direction} equal", d["result"] == "equal", d)
    check(f"{direction} counts", d["left_event_count"] == 2
          and d["right_event_count"] == 2, d)
    check(f"{direction} no difference", d["first_difference"] is None, d)

# 2. a third interleaving in the middle is equal as well.
s_mix = new_session()
steps(s_mix, FULL_MIX)
r = compare(s_ab, s_mix, 4, 4)
check("mixed interleave 200", r.status_code == 200)
check("mixed interleave equal",
      all(r.json()["directions"][d]["result"] == "equal"
          for d in ("A_to_B", "B_to_A")), r.json())

# 3. strict prefix: right only fired A's send [a1]; both directions are shorter
#    on the right (A_to_B missing the receive, B_to_A completely missing).
s_pref = new_session()
steps(s_pref, ["a1"])
r = compare(s_ab, s_pref, 4, 1)
check("prefix 200", r.status_code == 200)
d = r.json()["directions"]
check("prefix A_to_B right_prefix", d["A_to_B"]["result"] == "right_prefix",
      d["A_to_B"])
check("prefix A_to_B counts", d["A_to_B"]["left_event_count"] == 2
      and d["A_to_B"]["right_event_count"] == 1)
diff = d["A_to_B"]["first_difference"]
check("prefix A_to_B position 2", diff["position"] == 2, diff)
check("prefix A_to_B event",
      diff["left_event"] == {"action": "receive", "message": "m"}, diff)
# b1 is original trace step 3 in [a1, b2, b1, a2]
check("prefix A_to_B left step", diff["left_step"] == {"index": 3, "transition_id": "b1"})
check("prefix A_to_B right missing",
      diff["right_event"] is None and diff["right_step"] is None)
check("prefix B_to_A right_prefix", d["B_to_A"]["result"] == "right_prefix",
      d["B_to_A"])
diff = d["B_to_A"]["first_difference"]
check("prefix B_to_A position 1 counts",
      diff["position"] == 1 and d["B_to_A"]["left_event_count"] == 2
      and d["B_to_A"]["right_event_count"] == 0, diff)
check("prefix B_to_A event",
      diff["left_event"] == {"action": "send", "message": "n"}, diff)
# b2 is original trace step 2
check("prefix B_to_A left step", diff["left_step"] == {"index": 2, "transition_id": "b2"})

# mirrored: the shorter trace is on the left -> left_prefix, missing side null
r = compare(s_pref, s_ab, 1, 4)
d = r.json()["directions"]
check("left prefix result", d["B_to_A"]["result"] == "left_prefix", d["B_to_A"])
diff = d["B_to_A"]["first_difference"]
check("left prefix missing side", diff["left_event"] is None and diff["left_step"] is None)
check("left prefix right event", diff["right_event"] == {"action": "send", "message": "n"})
check("left prefix right step",
      diff["right_step"] == {"index": 2, "transition_id": "b2"})

# 4. partial prefix inside a direction preserving original order: right fired
#    only [b2], i.e. B_to_A has just the first send.
s_half = new_session()
steps(s_half, ["b2"])
r = compare(s_ab, s_half, 4, 1)
d = r.json()["directions"]
check("half A_to_B right_prefix", d["A_to_B"]["result"] == "right_prefix", d["A_to_B"])
check("half A_to_B counts", d["A_to_B"]["left_event_count"] == 2
      and d["A_to_B"]["right_event_count"] == 0)
check("half B_to_A right_prefix", d["B_to_A"]["result"] == "right_prefix", d["B_to_A"])
check("half B_to_A counts", d["B_to_A"]["left_event_count"] == 2
      and d["B_to_A"]["right_event_count"] == 1)
diff = d["B_to_A"]["first_difference"]
check("half position 2", diff["position"] == 2, diff)
check("half left event", diff["left_event"] == {"action": "receive", "message": "n"})
check("half left step original 4",
      diff["left_step"] == {"index": 4, "transition_id": "a2"}, diff)
check("half right missing", diff["right_event"] is None and diff["right_step"] is None)

# 5. divergence in message content with the same per-direction prefix length:
#    the right side exchanges p instead of n in the B->A direction.
s_div = new_session()
steps(s_div, ["a1", "b3", "a3", "b1"])  # B->A: send p, receive p
r = compare(s_ab, s_div, 4, 4)
check("divergence 200", r.status_code == 200)
d = r.json()["directions"]
check("divergence A_to_B equal", d["A_to_B"]["result"] == "equal", d["A_to_B"])
check("divergence B_to_A divergent", d["B_to_A"]["result"] == "divergent", d["B_to_A"])
diff = d["B_to_A"]["first_difference"]
check("divergence position 1", diff["position"] == 1, diff)
check("divergence left event",
      diff["left_event"] == {"action": "send", "message": "n"}, diff)
check("divergence right event",
      diff["right_event"] == {"action": "send", "message": "p"}, diff)
check("divergence left step", diff["left_step"] == {"index": 2, "transition_id": "b2"})
check("divergence right step",
      diff["right_step"] == {"index": 2, "transition_id": "b3"})

# divergence at the SECOND exchange position (per-direction event 3) after a
# shared two-event prefix: same [send n, receive n], then q vs r.
s_div2 = new_session()
steps(s_div2, FULL_AB + ROUND_R)  # B_to_A: send n, receive n, send r, receive r
r = compare(s_ab, s_div2, 4, 6)
d = r.json()["directions"]
# left has the first exchange only -> left is a strict prefix of right here;
# use a q-trace for an actual same-length divergence at position 3.
s_q = new_session()
steps(s_q, FULL_AB + ROUND_Q)
r = compare(s_q, s_div2, 6, 6)
d = r.json()["directions"]
check("second-round divergence", d["B_to_A"]["result"] == "divergent"
      and d["B_to_A"]["first_difference"]["position"] == 3, d["B_to_A"])
diff = d["B_to_A"]["first_difference"]
check("second-round events",
      diff["left_event"] == {"action": "send", "message": "q"}
      and diff["right_event"] == {"action": "send", "message": "r"}, diff)
check("second-round steps",
      diff["left_step"] == {"index": 5, "transition_id": "b4"}
      and diff["right_step"] == {"index": 5, "transition_id": "b6"}, diff)
check("second-round A equal", d["A_to_B"]["result"] == "equal")
# prefix check still works against the longer r-trace: first exchange is a
# strict prefix in B_to_A (and equal in A_to_B).
r = compare(s_ab, s_div2, 4, 6)
d = r.json()["directions"]
check("prefix before second round", d["B_to_A"]["result"] == "left_prefix", d)
check("prefix before second round pos",
      d["B_to_A"]["first_difference"]["position"] == 3, d["B_to_A"])
check("prefix before second round missing",
      d["B_to_A"]["first_difference"]["left_event"] is None)
check("prefix before second round right",
      d["B_to_A"]["first_difference"]["right_event"]
      == {"action": "send", "message": "r"})

# 6. different protocols are allowed: replay each trace under its own current
#    protocol; transition ids are ignored, only action/message compared.
s_other = new_session(PRENAMED)
steps(s_other, FULL_AB)
r = compare(s_ab, s_other, 4, 4)
check("cross protocol 200", r.status_code == 200, (r.status_code, r.text))
d = r.json()["directions"]
check("cross protocol A equal", d["A_to_B"]["result"] == "equal")
check("cross protocol B divergent", d["B_to_A"]["result"] == "divergent")
diff = d["B_to_A"]["first_difference"]
check("cross protocol messages", diff["left_event"]["message"] == "n"
      and diff["right_event"]["message"] == "ZZ", diff)
# same transition ids, different message -> still divergent, ids reported
check("cross protocol same ids ignored",
      diff["left_step"]["transition_id"] == "b2"
      and diff["right_step"]["transition_id"] == "b2", diff)

# 7. identical id on both sides compares the session against itself.
r = compare(s_ab, s_ab, 4, 4)
check("same session 200", r.status_code == 200, r.text)
check("same session equal both",
      all(r.json()["directions"][d]["result"] == "equal"
          for d in ("A_to_B", "B_to_A")))
# same id, one stale expectation -> 409 naming that side
r = compare(s_ab, s_ab, 4, 3)
check("same session stale 409", r.status_code == 409)
check("same session stale side",
      r.json()["detail"] == {"error": "version_conflict", "current_version": 4,
                             "session": "right", "session_id": s_ab}, r.json())
r = compare(s_ab, s_ab, 2, 4)
check("same session stale left 409", r.status_code == 409
      and r.json()["detail"]["session"] == "left")

# 8. two empty traces are equal in both directions, counts zero, no difference.
e1, e2 = new_session(), new_session()
r = compare(e1, e2, 0, 0)
check("two empty 200", r.status_code == 200)
for direction in ("A_to_B", "B_to_A"):
    check(f"empty {direction}", r.json()["directions"][direction] == {
        "left_event_count": 0, "right_event_count": 0,
        "result": "equal", "first_difference": None},
        r.json()["directions"][direction])

# 9. unknown sessions -> 404 (either side or both)
r = compare("deadbeef", e2, 0, 0)
check("unknown left 404", r.status_code == 404 and r.json() == {"detail": "unknown session"})
r = compare(e1, "deadbeef", 0, 0)
check("unknown right 404", r.status_code == 404)
r = compare("deadbeef", "deadbeef", 0, 0)
check("both unknown 404", r.status_code == 404)

# 10. stale expected version -> 409 naming the stale side and current version
r = compare(s_ab, s_ba, 3, 4)
check("stale left 409", r.status_code == 409, r.text)
check("stale left detail", r.json()["detail"] == {
    "error": "version_conflict", "current_version": 4,
    "session": "left", "session_id": s_ab}, r.json())
r = compare(s_ab, s_ba, 4, 1)
check("stale right 409", r.status_code == 409)
check("stale right detail", r.json()["detail"] == {
    "error": "version_conflict", "current_version": 4,
    "session": "right", "session_id": s_ba}, r.json())

# 11. malformed bodies -> 422
for bad_body, name in [
    ({"right_session_id": e2, "left_expected_version": 0,
      "right_expected_version": 0}, "missing left id"),
    ({"left_session_id": e1, "left_expected_version": 0,
      "right_expected_version": 0}, "missing right id"),
    ({"left_session_id": e1, "right_session_id": e2,
      "right_expected_version": 0}, "missing left version"),
    ({"left_session_id": e1, "right_session_id": e2,
      "left_expected_version": 0}, "missing right version"),
    ({"left_session_id": "", "right_session_id": e2,
      "left_expected_version": 0, "right_expected_version": 0}, "empty left id"),
    ({"left_session_id": e1, "right_session_id": "",
      "left_expected_version": 0, "right_expected_version": 0}, "empty right id"),
    ({"left_session_id": e1, "right_session_id": e2,
      "left_expected_version": -1, "right_expected_version": 0}, "negative left version"),
    ({"left_session_id": e1, "right_session_id": e2,
      "left_expected_version": 0, "right_expected_version": -1}, "negative right version"),
    ({"left_session_id": True, "right_session_id": e2,
      "left_expected_version": 0, "right_expected_version": 0}, "boolean left id"),
    ({"left_session_id": e1, "right_session_id": e2,
      "left_expected_version": 0, "right_expected_version": 0,
      "extra": 1}, "extra field"),
]:
    r = client.post("/v1/trace-comparisons", json=bad_body)
    check(f"{name} 422", r.status_code == 422, (r.status_code, r.text))

# 12. comparison never modifies data or consumes versions
before_l = client.get(f"/v1/sessions/{s_ab}").json()
before_r = client.get(f"/v1/sessions/{s_ba}").json()
compare(s_ab, s_ba, 4, 4)
compare(s_ab, s_ba, 4, 4)
after_l = client.get(f"/v1/sessions/{s_ab}").json()
after_r = client.get(f"/v1/sessions/{s_ba}").json()
check("left untouched", before_l["version"] == after_l["version"]
      and [s["transition_id"] for s in before_l["trace"]]
      == [s["transition_id"] for s in after_l["trace"]])
check("right untouched", before_r["version"] == after_r["version"]
      and [s["transition_id"] for s in before_r["trace"]]
      == [s["transition_id"] for s in after_r["trace"]])

# 13. per-direction order survives opposite interleavings: the two remaining
#     interleavings also compare equal to each other.
r = compare(s_ba, s_mix, 4, 4)
check("ba vs mix equal",
      all(r.json()["directions"][d]["result"] == "equal"
          for d in ("A_to_B", "B_to_A")), r.json())

# 14. endpoint states differ but action/message events are the same -> equal.
s_shape = new_session(PRESHAPED)
steps(s_shape, FULL_AB)
r = compare(s_ab, s_shape, 4, 4)
check("state-only changes 200", r.status_code == 200, r.text)
check("state-only changes equal",
      all(r.json()["directions"][d]["result"] == "equal"
          for d in ("A_to_B", "B_to_A")), r.json())
check("state-only version echoed",
      r.json()["right_version"] == 4)

# 15. concurrency: compare vs a concurrent execution — every comparison either
#     reads the complete committed snapshot at the expected version (200) or is
#     rejected as stale (409); a response never mixes two trace revisions.
s_con = new_session()
steps(s_con, ["a1"])  # version 1: A_to_B [send m], B_to_A empty
results = {}
barrier = threading.Barrier(6)


def compare_worker(tag):
    barrier.wait()
    rr = compare(s_con, s_ab, 1, 4)  # s_con is the shorter, LEFT side
    signature = None
    if rr.status_code == 200:
        d = rr.json()["directions"]
        signature = (
            d["A_to_B"]["result"], d["A_to_B"]["left_event_count"],
            d["B_to_A"]["result"], d["B_to_A"]["left_event_count"],
        )
    results[tag] = (rr.status_code, signature)


def execute_worker():
    barrier.wait()
    results["execute"] = run(s_con, "b2", 1).status_code


# committed version 1 whole: A_to_B has only [send m] (left prefix, count 1),
# B_to_A is empty (left prefix, count 0).
V1_SIGNATURE = ("left_prefix", 1, "left_prefix", 0)
threads = [threading.Thread(target=compare_worker, args=(f"cmp{i}",))
           for i in range(5)]
threads.append(threading.Thread(target=execute_worker))
for t in threads:
    t.start()
for t in threads:
    t.join()
check("racing execution 200", results["execute"] == 200, results)
for i in range(5):
    code, signature = results[f"cmp{i}"]
    check(f"race cmp{i} 200/409", code in (200, 409), results)
    check(f"race cmp{i} complete snapshot",
          code == 409 or signature == V1_SIGNATURE, results)
final = client.get(f"/v1/sessions/{s_con}").json()
check("raced session version 2", final["version"] == 2
      and [s["transition_id"] for s in final["trace"]] == ["a1", "b2"])

# 16. concurrent protocol replacement vs comparison at the same version: the
#     replace commits version 5; any 200 still reflects the complete old
#     revision (events n, hence equal), and post-commit readers get 409.
s_rep = new_session()
steps(s_rep, FULL_AB)
results = {}
barrier = threading.Barrier(6)


def replace_worker():
    barrier.wait()
    rr = client.put(f"/v1/sessions/{s_rep}/protocol",
                    json={"protocol": PRENAMED, "expected_version": 4})
    results["replace"] = rr.status_code


def make_compare_worker(tag):
    def worker():
        barrier.wait()
        rr = compare(s_rep, s_ab, 4, 4)
        result_value = None
        if rr.status_code == 200:
            result_value = rr.json()["directions"]["B_to_A"]["result"]
        results[tag] = (rr.status_code, result_value)
    return worker


threads = [threading.Thread(target=replace_worker)]
threads += [threading.Thread(target=make_compare_worker(f"cmp{i}")) for i in range(5)]
for t in threads:
    t.start()
for t in threads:
    t.join()
check("racing replace 200", results["replace"] == 200, results)
for i in range(5):
    code, result_value = results[f"cmp{i}"]
    check(f"replace race cmp{i} 200/409", code in (200, 409), results)
    check(f"replace race cmp{i} coherent revision",
          code == 409 or result_value == "equal", results)

# 17. old endpoints and session features remain intact
r = client.get("/healthz")
check("healthz intact", r.status_code == 200 and r.json() == {"status": "ok"})
r = client.post("/v1/deadlocks", json={
    "A": {"states": [0, 1], "initial": 0, "finals": [1]},
    "B": {"states": [0, 1], "initial": 0, "finals": [1]},
    "transitions": [
        {"id": "t1", "from": "A", "to": "B", "action": "send", "message": "m",
         "source": 0, "target": 1},
        {"id": "t4", "from": "A", "to": "B", "action": "receive", "message": "m",
         "source": 0, "target": 1},
    ],
})
check("deadlocks intact", r.status_code == 200)
r = client.get(f"/v1/sessions/{s_ab}")
check("session read intact", r.status_code == 200 and r.json()["version"] == 4)
r = client.post(f"/v1/sessions/{s_pref}/transitions",
                json={"transition_id": "b2", "expected_version": 1})
check("execution intact", r.status_code == 200 and r.json()["version"] == 2)

print()
if failures:
    print(f"{len(failures)} FAILURES: {failures}")
    raise SystemExit(1)
print("ALL CHECKS PASSED")
