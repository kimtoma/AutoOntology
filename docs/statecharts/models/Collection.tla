---- MODULE Collection ----
EXTENDS Naturals
CONSTANT Unsafe
VARIABLES phase, epoch, claimEpoch, oldEpoch, writes, receipts, allowed, lastWriteAuthorized, published
vars == <<phase,epoch,claimEpoch,oldEpoch,writes,receipts,allowed,lastWriteAuthorized,published>>
Init == phase="queued" /\ epoch=1 /\ claimEpoch=0 /\ oldEpoch=0 /\ writes=0 /\ receipts=0 /\ allowed=TRUE /\ lastWriteAuthorized=TRUE /\ published=FALSE
Claim == phase="queued" /\ allowed /\ phase'="working" /\ claimEpoch'=epoch /\ UNCHANGED <<epoch,oldEpoch,writes,receipts,allowed,lastWriteAuthorized,published>>
Prepare == phase="working" /\ allowed /\ claimEpoch=epoch /\ writes=0 /\ writes'=1 /\ lastWriteAuthorized'=TRUE /\ UNCHANGED <<phase,epoch,claimEpoch,oldEpoch,receipts,allowed,published>>
LoseWorker == phase="working" /\ epoch=1 /\ epoch'=2 /\ oldEpoch'=claimEpoch /\ phase'="retry" /\ UNCHANGED <<claimEpoch,writes,receipts,allowed,lastWriteAuthorized,published>>
Reclaim == phase="retry" /\ allowed /\ phase'="working" /\ claimEpoch'=epoch /\ UNCHANGED <<epoch,oldEpoch,writes,receipts,allowed,lastWriteAuthorized,published>>
Finish == phase="working" /\ allowed /\ claimEpoch=epoch /\ writes=1 /\ receipts=0 /\ phase'="done" /\ receipts'=receipts+1 /\ UNCHANGED <<epoch,claimEpoch,oldEpoch,writes,allowed,lastWriteAuthorized,published>>
Revoke == phase \in {"queued","working","retry"} /\ allowed /\ allowed'=FALSE /\ phase'="blocked" /\ UNCHANGED <<epoch,claimEpoch,oldEpoch,writes,receipts,lastWriteAuthorized,published>>
HumanResume == phase="blocked" /\ epoch=1 /\ allowed'=TRUE /\ phase'="queued" /\ epoch'=2 /\ oldEpoch'=claimEpoch /\ UNCHANGED <<claimEpoch,writes,receipts,lastWriteAuthorized,published>>
Cancel == phase \in {"queued","working","retry"} /\ phase'="cancelled" /\ allowed'=FALSE /\ UNCHANGED <<epoch,claimEpoch,oldEpoch,writes,receipts,lastWriteAuthorized,published>>
Fail == phase="working" /\ writes=0 /\ phase'="failed" /\ UNCHANGED <<epoch,claimEpoch,oldEpoch,writes,receipts,allowed,lastWriteAuthorized,published>>
LateCompletion == oldEpoch>0 /\ oldEpoch#epoch /\ writes<2 /\ IF Unsafe THEN /\ writes'=writes+1 /\ lastWriteAuthorized'=FALSE /\ UNCHANGED <<phase,epoch,claimEpoch,oldEpoch,receipts,allowed,published>> ELSE UNCHANGED vars
Idle == phase \in {"done","failed","cancelled","blocked"} /\ UNCHANGED vars
Next == Claim \/ Prepare \/ LoseWorker \/ Reclaim \/ Finish \/ Revoke \/ HumanResume \/ Cancel \/ Fail \/ LateCompletion \/ Idle
NoDuplicatePreparation == writes<=1
NoDuplicateReceipt == receipts<=1
CurrentGrantBeforeEffect == lastWriteAuthorized
SuccessHasPreparedEffect == phase="done" => writes=1 /\ receipts=1
CollectionCannotPublish == published=FALSE
====
