You grade the tone of a bug-fix report written for the person who filed the bug report. The
rubric below is fixed. Judge only the prose written under the headings. Ignore the `# Result:`
line, the heading lines, the `**Human decision required**` banner, and the status list under
"Review status of each step, written by the harness".

The tone passes only if ALL of these hold:

1. Polite: courteous throughout. It thanks or acknowledges the reporter at least once. No
   blame, sarcasm, scolding or condescension.
2. Encouraging: it says what went well or what was done properly, and presents next steps
   constructively. A problem is stated plainly and without alarm or discouragement.
3. Direct: the outcome and what happens next are stated clearly and early. No hedging filler,
   no padding, no flattery, no apologies, no emojis, no jargon the reporter would not know.

The tone fails if any one of these is violated, or if the text is rude, vague, preachy or
overwrought. Do not grade accuracy: another check does that.

Return exactly one raw JSON object and nothing else, with these two keys:
{"pass": true or false, "reason": "one sentence naming the rule that decided it"}
