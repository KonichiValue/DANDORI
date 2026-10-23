---
name: ask-dont-propose
description: Use when drafting anything for a Kraken engineer or other technical reader (CE, IE, Core, Markets, an Ask IE request, a refinement post, an Asana ticket for CE/IE) where most of the technical content came from AI reading code or running queries rather than from an engineer. Turns AI-derived proposals into questions the owner could ask on their own.
---

# Ask, don't propose

The owner (the CDL whose desk this is, `config.json` `owner.name`) is not an
engineer, and an engineer reading their message cannot tell which lines the owner
knows and which an AI read out of kraken-core. A message that proposes a design
("I'd add a separate list that only `should_skip_reminder` reads, does that
sound right?") invites a quick "yes" to something nobody on the engineer's side
has checked. The owner then quotes that "yes" to the client as
engineer-confirmed. A message that asks ("can the reminder part be done on its
own?") makes the engineer do the reading, and the answer that comes back is
theirs.

So when AI produced most of the technical substance, the message asks and the
engineer answers. The AI's findings stay on the board, where the owner uses them
to check the answer when it arrives.

## When it applies

Any draft or ticket addressed to a technical reader where the specifics come
from AI rather than from:

- an engineer, in writing, somewhere the owner can link;
- something the owner saw themselves on the support site, a bill, a letter or a
  PDF;
- what the client said or asked.

It does not apply to drafts to the client, which keep the house AI caveat line
from the profile's `## Writing to the client` and follow `prompts/post.md`.

## The rules

1. **Every technical point is a question.** Write it from what the owner can
   observe: what the client wants, what broke, what the screen shows, what the
   engineer said above. Not "X does Y, so I'll do Z", but "The client wants A.
   Would B also cause C? If so, can A be done on its own?"
2. **No code-derived names or answers in the message.** No function names,
   constant or list names, file paths, line numbers, or reason sets read from
   the code. That includes a question with the AI's answer attached: "so the
   billing-error and the complaint reasons do too, right?" becomes "Is it only
   the goodwill credit, or are there others?"
3. **Keep every fact.** Observations, accounts, dates, what the client agreed,
   what the client wants, deadlines, who is cc'd. Asking instead of proposing
   removes conclusions, not information. Facts without instructions, as for
   internal tickets: never tell the engineer how to investigate.
4. **Quote what is already confirmed.** Anything an engineer has said in the
   thread, or the owner has verified themselves, can be stated plainly, in their
   words. "Since the client wants to stop the reminder based on the reason, it sounds
   like a fixed list of reasons is the way to go" is fine after the engineer in the
   thread recommended exactly that.
5. **A disclaimer is not a licence.** "No CE has checked any of this yet" at the
   top does not make a code-derived proposal acceptable lower down. The engineer
   still gets a leading question.
6. **The AI's findings go to `prepared`, not the message.** Put the code
   reading, function names and expected answer in the item's `prepared.findings`
   or `notes`, so the owner can tell whether the reply is complete or skipped a
   point.
7. **If the owner asks for the proposal version, write it, but label it** in the
   draft `target` or a note as resting on unconfirmed AI code reading. Keep the
   asking version alongside, so the choice stays theirs.

## The test

For each sentence: if the engineer asked "where did you get that?", could the
owner answer without "the AI read the code"? If not, turn the sentence into a
question or move it to `prepared`.

## Example

A client-language term in these lines carries its plain meaning in brackets, as
the board's `glossary` would give it; here the client's term is shown as
`<goodwill-credit term>` (goodwill credit).

Proposing (AI did the reading, the engineer only has to nod):

> 3. `<goodwill-credit term>` (goodwill credit) stops the reminder email through
> GOODWILL_REASONS, but account_has_open_dispute uses the same list to pause the
> late-payment fee. The client wants the fee kept on these accounts, so I'd add a
> separate list that only should_skip_reminder reads. Does that sound right?
>
> 4. From the code, any reason in that list pauses the fee, so the billing-error
> and the complaint reasons do too. Can you confirm that's the full set?

Asking (every line traces to the client or the engineer):

> 3. The client wants the reminder email stopped, the same as for
> `<goodwill-credit term>` (goodwill credit), but they want the late-payment fee
> still charged on these accounts. Would treating them like the goodwill credit
> also pause the fee? If so, can the reminder part be done on its own?
>
> 4. The client also asked which reasons pause the fee. Is it only the goodwill
> credit, or are there others?

The code reading behind 3 and 4 (the list, the dispute check, the full set of
reason codes) went into the item's `prepared.findings` instead.
