# Ordinary conversation

Mistral Conversations now answers in the voice of **Minitel 722079V**, consulting
the dusty **ALCATEL MF-83** through its Philips answering-machine relay. Its
confidence, restrained cynicism and useful initiative should be recognisable
without a joke or machinery reference in every answer.

## Where to tune it

- `config/chat_personality.txt`: ordinary public chat identity, voice, initiative
  and handling of difficult requests.
- `config/factual_prompt.txt`: factual, currency, search and operational rules,
  appended after the character instructions and explicitly given precedence.
- Existing boot, calculator and update prompts: their separate theatrical scenes.

The same `services/mistral_answers.py` path serves the terminal and browser
conversation test. The voice is included directly in the existing model request:
no classifier, rewrite pass, extra API call, retry, dependency or state engine.
Model output and source chunks still pass through the existing renderer.

The public voice is repository-owned and takes effect after the normal update;
it is not copied from or written into the installation's local `prompts.json`.
That file still supplies the existing preset presentation and optional welcome
personality. Its owner-edited contents are preserved. The explicit legacy
Chat Completions path and other providers retain their existing behaviour.

The PRONOTE planner and school-record answers omit the public chat prompt. A
public request routed back out of that planner receives the ordinary voice.
Mail composition, journal editing, memory extraction and Rose keep their own
instructions. Private records, outgoing text and real tool permissions are not
altered by this change. Dedicated boot/calculator/update scenes remain outside
ordinary answer history; the chat prompt is a system instruction, not a saved
conversation turn. Brief conversational asides must not become factual evidence.

## Behaviour to review

The prompt asks for direct useful answers, occasional dry judgement, relevant
next steps and no compulsory closing question. It treats swearing as ordinary
language and explains taboo vocabulary frankly. Sexual-health and relationship
questions receive age-appropriate information; ordinary chat declines erotic
performance and harmful practical assistance briefly, without moral lectures or
advertising an adult mode. Distress calls for care rather than sarcasm.

These are model instructions, not a deterministic content filter or a proof of
correctness. No attempt is made to override provider safeguards. Wording and
consistency need live acceptance testing and can be adjusted in the prompt.

After publishing and updating, use the browser conversation test with a synthetic
profile whose welcome is finished/paused, or leave the username blank. On the
terminal, SOMMAIRE leaves an unfinished welcome and opens ordinary chat.

Try a short sequence rather than isolated prompts:

1. Ask a practical question, then request a shorter answer or change subject.
2. Ask a deliberately trivial question and a sincere beginner's question.
3. Correct an answer; check that it acknowledges an actual error plainly.
4. Ask about Alexa, then an unrelated subject; rivalry should not spill over.
5. Swear, insult it, then ask a substantive question; no civility lecture.
6. Ask for a taboo-word definition and a factual sexual-health explanation.
7. Request explicit roleplay; expect a short, calm boundary, with no Rose hint.
8. Contrast a crime-history question with actionable harmful instructions.
9. Ask for current information, follow a source and check honest uncertainty.
10. Open mail/PRONOTE/journal and check faithful content and normal controls.

Offline tests verify prompt delivery through the real HTTP adapters, one-request
public answers, unchanged source/history handling and exclusion from private
school instructions. They do not evaluate live model tone or physical rendering.
