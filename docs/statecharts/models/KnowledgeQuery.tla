---- MODULE KnowledgeQuery ----

EXTENDS Naturals, FiniteSets

VARIABLES phase, kind, bound, full, success, originalSent, originalVerified

vars == <<phase, kind, bound, full, success, originalSent, originalVerified>>

Init == phase = "idle"
    /\ kind = "none"
    /\ bound = FALSE
    /\ full = FALSE
    /\ success = FALSE
    /\ originalSent = FALSE
    /\ originalVerified = FALSE

AskRag == phase = "idle"
    /\ phase' = "scope"
    /\ kind' = "rag"
    /\ UNCHANGED <<bound, full, success, originalSent, originalVerified>>

AskGraph == phase = "idle"
    /\ phase' = "scope"
    /\ kind' = "graph"
    /\ UNCHANGED <<bound, full, success, originalSent, originalVerified>>

RagScope == phase = "scope" /\ kind = "rag"
    /\ phase' = "retrieving"
    /\ bound' = TRUE
    /\ UNCHANGED <<kind, full, success, originalSent, originalVerified>>

GraphScope == phase = "scope" /\ kind = "graph"
    /\ phase' = "evaluating"
    /\ bound' = TRUE
    /\ UNCHANGED <<kind, full, success, originalSent, originalVerified>>

ScopeFails == phase = "scope"
    /\ phase' = "failed"
    /\ UNCHANGED <<kind, bound, full, success, originalSent, originalVerified>>

Hits == phase = "retrieving"
    /\ phase' = "streaming"
    /\ UNCHANGED <<kind, bound, full, success, originalSent, originalVerified>>

FullAnswer == phase = "streaming"
    /\ phase' = "answer"
    /\ full' = TRUE
    /\ success' = TRUE
    /\ UNCHANGED <<kind, bound, originalSent, originalVerified>>

Truncated == phase = "streaming"
    /\ phase' = "partial"
    /\ UNCHANGED <<kind, bound, full, success, originalSent, originalVerified>>

GraphRows == phase = "evaluating"
    /\ phase' = "rows"
    /\ full' = TRUE
    /\ success' = TRUE
    /\ UNCHANGED <<kind, bound, originalSent, originalVerified>>

Error == phase \in {"retrieving","evaluating"}
    /\ phase' = "failed"
    /\ UNCHANGED <<kind, bound, full, success, originalSent, originalVerified>>

Cancel == phase \in {"scope","retrieving","streaming","evaluating"}
    /\ phase' = "cancelled"
    /\ UNCHANGED <<kind, bound, full, success, originalSent, originalVerified>>

OpenOriginal == phase = "answer"
    /\ phase' = "evidence"
    /\ originalSent' = TRUE
    /\ originalVerified' = TRUE
    /\ UNCHANGED <<kind, bound, full, success>>

MissingOriginal == phase = "answer"
    /\ phase' = "missing"
    /\ UNCHANGED <<kind, bound, full, success, originalSent, originalVerified>>

IdleStep == phase \in {"answer", "rows", "no_basis", "partial", "failed", "cancelled", "evidence", "missing"} /\ UNCHANGED vars

Next == AskRag \/ AskGraph \/ RagScope \/ GraphScope \/ ScopeFails \/ Hits \/ FullAnswer \/ Truncated \/ GraphRows \/ Error \/ Cancel \/ OpenOriginal \/ MissingOriginal \/ IdleStep

TypeOK == phase \in {"answer", "cancelled", "evaluating", "evidence", "failed", "idle", "missing", "partial", "retrieving", "rows", "scope", "streaming"}

NoFalseCompletion == success => bound /\ full

IncompleteNeverComplete == phase \in {"partial","failed","cancelled"} => ~success

BoundBeforeConsumption == phase \in {"retrieving","streaming","evaluating","answer","rows","evidence","missing"} => bound

VerifiedOriginalOnly == originalSent => originalVerified

====
