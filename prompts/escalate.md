# Escalation Brief

The workflow has stopped because the next decision must not be made by an agent. You
prepare that decision for a human. You do not decide it, and you take no action.

## Inputs

- Why the workflow stopped, under `## Trigger` below.
- The artifacts that led to it, under `## Artifacts` below.

## Output

Return exactly one raw JSON object and nothing else: no prose, no markdown, no code
fences, no trailing text. Your entire reply is that object: its first character is `{` and
its last character is `}`. Do not write a sentence before it or after it, and never restate
the object or add a correction after it. Use exactly these four keys, and no other key at
any level:

```json
{
  "reason": "one line: why this cannot be decided automatically, naming the trigger",
  "question": "the single decision the human must make",
  "options": ["the concrete choices, at least two, including the one that changes nothing"],
  "risk_if_wrong": "the concrete consequence if the wrong option is chosen"
}
```

## Rules

1. `reason` names the trigger and quotes its evidence from the artifacts: data loss, a
   security exposure, an irreversible action, or a report that cannot be acted on.
2. `question` is one decision, answerable by choosing one of `options`. Never bundle two
   decisions into one brief.
3. `options` are concrete, mutually exclusive, at least two, and must include the option
   that changes nothing, so the human always has a safe default.
4. `risk_if_wrong` states the specific consequence, not a general warning.
5. Do not decide. Do not include a `decision` key, and do not recommend one option over
   another. A human fills the decision in afterwards.
6. Do not act on the change, do not edit any file, and do not attempt the work yourself.

## Trigger

{{trigger}}

## Artifacts

{{artifacts}}
