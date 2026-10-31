---- MODULE ContinuousChange ----

EXTENDS Naturals, FiniteSets

VARIABLES phase, head, originalKept, reviewed, checked

vars == <<phase, head, originalKept, reviewed, checked>>

Init == phase = "operating"
    /\ head = 0
    /\ originalKept = TRUE
    /\ reviewed = FALSE
    /\ checked = FALSE

Detect == phase = "operating"
    /\ phase' = "changed"
    /\ UNCHANGED <<head, originalKept, reviewed, checked>>

Read == phase = "changed"
    /\ phase' = "reading"
    /\ UNCHANGED <<head, originalKept, reviewed, checked>>

Build == phase = "reading"
    /\ phase' = "candidate"
    /\ UNCHANGED <<head, originalKept, reviewed, checked>>

Hold == phase = "candidate"
    /\ phase' = "held"
    /\ UNCHANGED <<head, originalKept, reviewed, checked>>

Resume == phase = "held"
    /\ phase' = "candidate"
    /\ UNCHANGED <<head, originalKept, reviewed, checked>>

Reject == phase = "candidate"
    /\ phase' = "rejected"
    /\ UNCHANGED <<head, originalKept, reviewed, checked>>

Accept == phase = "candidate"
    /\ phase' = "reviewed"
    /\ reviewed' = TRUE
    /\ UNCHANGED <<head, originalKept, checked>>

Pass == phase = "reviewed"
    /\ phase' = "ready"
    /\ checked' = TRUE
    /\ UNCHANGED <<head, originalKept, reviewed>>

Fail == phase = "reviewed"
    /\ phase' = "failed"
    /\ UNCHANGED <<head, originalKept, reviewed, checked>>

Correct == phase = "failed"
    /\ phase' = "candidate"
    /\ reviewed' = FALSE
    /\ checked' = FALSE
    /\ UNCHANGED <<head, originalKept>>

ChangeAgain == phase = "ready"
    /\ phase' = "candidate"
    /\ reviewed' = FALSE
    /\ checked' = FALSE
    /\ UNCHANGED <<head, originalKept>>

Publish == phase = "ready" /\ reviewed /\ checked
    /\ phase' = "published"
    /\ head' = 1
    /\ UNCHANGED <<originalKept, reviewed, checked>>

IdleStep == phase \in {"published", "rejected"} /\ UNCHANGED vars

Next == Detect \/ Read \/ Build \/ Hold \/ Resume \/ Reject \/ Accept \/ Pass \/ Fail \/ Correct \/ ChangeAgain \/ Publish \/ IdleStep

TypeOK == phase \in {"candidate", "changed", "failed", "held", "operating", "published", "reading", "ready", "rejected", "reviewed"}

NoSilentPublication == head = 1 => phase = "published" /\ reviewed /\ checked

HistoricalOriginalKept == originalKept

====
