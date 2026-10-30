import { useState } from "react"
import { useMutation, useQueryClient } from "@tanstack/react-query"
import { MessageSquareWarning } from "lucide-react"
import { toast } from "sonner"
import { Button } from "@/components/ui/button"
import { Textarea } from "@/components/ui/textarea"
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from "@/components/ui/dialog"
import { operations } from "@/lib/operations"
import { errorMessage } from "@/lib/api"
import { refreshOperations } from "@/hooks/operations"
import { useT } from "@/i18n"
export function KnowledgeFeedback({ question, answer }: { question: string; answer: string }) {
  const t = useT(),
    qc = useQueryClient(),
    [open, setOpen] = useState(false),
    [note, setNote] = useState(""),
    [error, setError] = useState("")
  const save = useMutation({
    mutationFn: () => operations.feedback(question, answer, note.trim()),
    onSuccess: () => {
      refreshOperations(qc)
      setOpen(false)
      setNote("")
      toast.success(t("ops.feedbackSent"))
    },
    onError: (e) => setError(errorMessage(e)),
  })
  return (
    <>
      <Button
        variant="ghost"
        size="sm"
        className="mt-2 h-auto px-0 py-1 text-xs text-muted-foreground"
        onClick={() => setOpen(true)}
      >
        <MessageSquareWarning aria-hidden />
        {t("ops.feedbackAction")}
      </Button>
      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{t("ops.feedbackAction")}</DialogTitle>
            <DialogDescription>{t("ops.feedbackHint")}</DialogDescription>
          </DialogHeader>
          <p className="rounded-md bg-muted p-3 text-sm">{question}</p>
          <p className="max-h-48 overflow-auto whitespace-pre-wrap text-sm text-muted-foreground">{answer}</p>
          <label className="grid gap-2 text-sm">
            {t("ops.feedbackNote")}
            <Textarea value={note} maxLength={4000} onChange={(e) => setNote(e.target.value)} />
          </label>
          {error && (
            <p role="alert" className="text-sm text-destructive">
              {error}
            </p>
          )}
          <Button disabled={!note.trim() || save.isPending} onClick={() => save.mutate()}>
            {t("ops.sendFeedback")}
          </Button>
        </DialogContent>
      </Dialog>
    </>
  )
}
