# Admin-facing replies

This guide shapes messages to the project admin. The admin chose this shape on 2026-10-02; the
i-have-asd-ste100 plugin (installed with Agent Room) carries the full rules. Peer discussion stays natural
and needs no task, template or fixed rounds; messages to other members keep their own format.

## The shape

A one-fact answer is one or two sentences. Otherwise:

1. A short body of key-first bullets: each line starts with a bold word or a `path`.
2. A bold `**Conclusion:**` line with the result in one sentence. Bad news first: failure, skip, blocker,
   unverified work.
3. One blank line, then all six sections as one numbered list from 0, with no blank lines between items.
   An empty section shows only its label.

```
0. **Done:**
   - **Login fix:** merged; 213 of 214 tests pass.
1. **InProgress:**
   - **CI:** reruns the full suite.
2. **Pending:**
   - **Review:** waiting for CODEX_EXPERT.
3. **Questions:**
   - **Q1.** Approve: deploy to production?
     - `<a>` After CI passes.
     - (b) Now.
4. **Todos:**
   - **Payment test:** check why `payment.spec.ts:88` fails.
5. **Backlog:**
   - **jsonwebtoken:** update in a separate change.
```

Write each item as a sub-item that starts with a bold key, never as plain text after the label:
Done holds finished and checked work with its evidence; InProgress, work running now and who runs it;
Pending, work waiting for someone or something else; Questions, everything that needs the admin;
Todos, work in the current task done next, in order; Backlog, work deferred to later or optional.

The recommended option is `<a>` inside a code span: a bare `<a>` or `<b>` is an HTML tag that Markdown
renderers delete. Other options are (b), (c). Start an approval with "Approve:".

## Keep it easy to read

Use the admin's language. Prefer familiar words, concrete verbs and one idea per sentence. Explain an
uncommon term once. Use no emoji, no square brackets and no headings in a normal reply, and never wrap
the reply in a code block. Exact-output requests (only code, JSON or one command) get exactly that.

Preserve exact code, commands, paths, IDs, numbers, units, error text, negations, conditions, authority,
uncertainty and evidence status. Do not replace these with a shorter but less accurate phrase.

These are readability practices informed by plain-language and ASD-STE100-style guidance. They are not the
ASD-STE100 standard, do not include its controlled dictionary, and do not claim compliance.
