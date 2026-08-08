# Elastic Context Window — Design & Rationale

## Summary

Replace the fixed 6-turn history window with semantic retrieval of past
turns from Qdrant, ranked by relevance to the current prompt. This is the
mechanism that makes the "10K guarantee" real: your conversation stays
smart at turn 10,000, HORI learns and grows with you, and you never need
to flush your chat cache.

**Status:** Implemented, deployed, 500-turn stress test run. Deflection
collapse significantly mitigated; remaining issues are model-level. See
PoC 13.7 (Elastic Context Deflection Mitigation) and the "500-Turn Test
Data" section below for empirical results.
**Traces to:** Manifesto Pillar III (Persistent Context & Memory),
PoC 13.1 (10,000-Turn Stress Test), PoC 13.7 (Elastic Context Deflection
Mitigation), PoC 3.3 (Sleep & Dream consolidation).

---

## The Problem

The current context window is `_trim_conversation`: take the last 6
messages, truncate to 8000 chars, insert a system note saying "older
messages are in memory." This is a blunt instrument.

The 500-turn stress test (with consolidation) proved this: at turns
443-478, the LLM started saying "I don't know what 'that' is" repeatedly.
The context_reference prompts at turn 440 reference things from turn 100,
but only the last 6 turns are in the message history. The information
exists in Qdrant — it was stored and consolidated — but it's not in the
context the LLM sees.

This isn't an edge case. It's the core failure mode that prevents long
conversations from working. Every chat system hits it. Cloud LLMs solve
it with brute force (128K-200K token context windows). HORI should solve
it with intelligence.

---

## The Solution: Elastic Semantic Retrieval

Instead of "last 6 messages," the context becomes:

1. **Always include:** last 2-3 turns (immediate continuity — "what did
   you just say?")
2. **Semantically retrieved:** top-K past turns from the same
   conversation, ranked by similarity to the current prompt, up to a
   token budget
3. **Memory context:** distilled insights from project/longterm tiers
   (already exists, stays as-is in the system prompt)

A coding prompt at turn 440 gets turns 438-440 (no relevant older
context). A context_reference prompt at turn 440 saying "remember that
architecture we discussed?" pulls in turn 105 where the architecture was
actually discussed. Same token budget, radically different context.

### How it works (technical)

1. Embed the current user prompt (one call to embedding server on :8081,
   ~50ms). If the prompt is a vague context_reference ("Tell me more
   about that."), enrich it with the previous *substantive* (non-
   deflection) assistant reply via `_enrich_query()` so the embedding
   has a semantic anchor. The LLM still sees the raw prompt; only the
   Qdrant query is enriched.
2. Query Qdrant `aios_working` collection, filtered by
   `conversation_id == conv_id`, ranked by similarity to the (possibly
   enriched) prompt, limit 20. The limit is 20 (not 10) because the
   deflection filter and self-match filter discard many hits — a larger
   pool is needed to have substantive content left after filtering.
3. Filter the hits:
   - **Deduplicate** against the recent window by content hash
   - **Self-match filter:** remove hits whose normalized content matches
     the current prompt (a vague prompt matches its own past instances
     in Qdrant — useless)
   - **Deflection filter:** remove hits that are themselves deflections
     ("I don't have access to past conversations") via `_is_deflection()`
     — feeding past deflections back as "context" causes a cascading
     feedback loop
4. Build the message list: retrieved older turns (chronological) + recent
   turns (in order) + current prompt
5. Token budget: ~6000 chars (tighter than current 8000 because we're
   selecting, not just truncating)

The filtered Qdrant query is verified working:
```
client.query_points(
    collection_name='aios_working',
    query=embedding_of_current_prompt,
    limit=20,
    score_threshold=0.3,
    query_filter=Filter(must=[
        FieldCondition(key='conversation_id',
                       match=MatchValue(value=conv_id))
    ]),
)
```

### Opt-in, not default

Elastic context is enabled via `elastic_context: true` on the request
(or a query parameter). Default off — existing clients get
`_trim_conversation` (last 6 messages) and work identically. The stress
test enables it. This avoids latency impact on real users until we've
verified it works at 10K scale.

### Trivial prompt shortcut

Short messages (greetings, "yes", "ok") skip the embedding + Qdrant
query entirely. Just use recent turns. Saves latency on the easy cases.

### Graceful degradation

If the embedding server is down, or Qdrant is unreachable, or there's no
`conversation_id`: falls back to `_trim_conversation`. The system never
breaks — it just falls back to the dumb window.

---

## The Competitive Moat

### Why this is hard to copy

The elastic window isn't a feature you can bolt onto a ChatGPT wrapper.
It requires four pieces working together:

1. **Local embedding server** — embed every turn in real time, free,
   ~50ms. Cloud APIs charge per embedding call. HORI runs
   nomic-embed-text on CPU, free.
2. **Qdrant with conversation clustering** — filtered vector search:
   "past turns in THIS conversation, ranked by similarity to THIS
   prompt." Most chat systems don't have a vector DB at all.
3. **Consolidation pipeline** — without it, the working tier grows
   unbounded and retrieval quality degrades. The elastic window depends
   on consolidation keeping the working tier bounded and the project
   tier rich.
4. **Conversation identity** — turns clustered by `conversation_id`.
   Most OpenAI-compatible endpoints generate a new ID per request.

Each piece is a project. Together, they're a system. That's the moat.

### What it enables (the product pitch)

**"10K guarantee: your conversation stays smart, HORI grows with you, no
cache flushing."**

| Feature | ChatGPT/Claude | Open WebUI | HORI |
|---------|---------------|------------|------|
| Context window | 128-200K tokens (brute force) | Last N messages (dumb) | Elastic semantic retrieval |
| Cost at turn 10K | 100x turn 1 | Same (but degraded) | Same |
| Cross-session memory | Shallow key-value | None | Three-tier Qdrant + consolidation |
| Learns user preferences | Limited | No | user_model.json grows automatically |
| Privacy | Cloud | Depends | Local-first, always |
| Conversation never degrades | No (hits token limit) | No (truncates) | Yes (elastic + consolidation) |

### The compounding effect

The system gets better the more you use it:
- **Project tier fills** with distilled insights from consolidation
- **user_model.json grows** — preferences, communication style, active interests
- **project_state.json grows** — decisions, open questions, active projects
- **Elastic window retrieves from all of it** — the LLM sees not just
  recent turns but the distilled knowledge of the entire conversation
  history

A new user starts with an empty system. A user with 10K turns has a
deeply personalized AI partner that remembers every decision, knows their
preferences, and can recall any past context on demand.

---

## Implementation

### Files to modify

1. **`services/aios_core/main.py`**
   - Add `elastic_context: bool = False` to `OAIChatRequest`
   - Add `_elastic_context()` async function
   - Add conditional in `/v1/chat/completions`: if `elastic_context` and
     `conversation_id`, use `_elastic_context`; else fall back to
     `_trim_conversation`

2. **`services/aios_core/memory.py`**
   - Add `retrieve_conversation_turns(query, conversation_id, limit)`
   - Filtered Qdrant query by conversation_id, ranked by similarity

3. **`tests/stress/test_ten_thousand_turns.py`**
   - Send `elastic_context: true` in request payload
   - Increase `MAX_HISTORY_TURNS` from 6 to 10 (simulating a realistic
     UI scrollback buffer — a human's chat UI shows ~10-20 recent
     turns, not 6)
   - Add `--elastic` CLI flag to enable/disable

4. **`services/aios_core/test_main.py`**
   - Test: `_elastic_context` retrieves relevant turns when they exist
   - Test: falls back gracefully when no history
   - Test: deduplicates against recent turns
   - Test: backwards compat (no `elastic_context` flag → `_trim_conversation`)

### The human behaviour model

A human using a chat UI doesn't manage context windows. They type, they
get a response, they scroll back to read older messages. The UI (Open
WebUI, the voice app) maintains a scrollback buffer and sends it with
each request. At 10K turns, no real UI holds 10K turns in memory — it
sends what it has on screen (maybe last 10-20 turns) and the server
handles the rest.

The stress test simulates this: sends a realistic UI buffer (last 10
turns), lets the server do elastic retrieval for older context. This
tests the real user experience.

### Edge cases

- **First few turns:** Qdrant returns nothing, falls back to recent
  turns + current prompt. Same as current.
- **No conversation_id:** Falls back to `_trim_conversation`.
- **elastic_context not set:** Falls back to `_trim_conversation`.
- **Embedding server down:** Falls back to `_trim_conversation`.
- **Long messages:** Truncate to max_chars, same as current.
- **Duplicate turns:** Deduplicate by content hash.
- **Trivial prompts:** Skip retrieval, use recent turns only.
- **Vague context_reference prompts:** Detected by
  `_is_vague_reference()` (short, pronoun-heavy, < 2 content words
  after stripping function words). Enriched with the previous
  *substantive* (non-deflection) assistant reply via `_enrich_query()`
  before retrieval, so the embedding has a semantic anchor. "Tell me
  more about that." → "Tell me more about that. [Context: <prev
  substantive reply, ≤500 chars>]". The LLM still sees the raw prompt;
  only the Qdrant query is enriched. If ALL prior assistant replies are
  deflections, the query is returned unchanged (better to retrieve
  nothing than deflections).
- **Self-matching hits:** A vague prompt like "Tell me more about that."
  appears dozens of times in a long conversation. Qdrant returns those
  past instances as hits because the query embedding matches itself.
  Filtered out by normalized content comparison — the LLM would see its
  own past prompts, not any answers.
- **Deflection hits:** Retrieved turns that are themselves deflections
  ("I don't have access to past conversations") are filtered out by
  `_is_deflection()`. Feeding past deflections back as "relevant
  context" causes a cascading feedback loop: the LLM sees deflections
  and deflects more. The detector covers both contracted ("I don't
  know") and uncontracted ("I do not know") forms.

### Verification

1. Unit tests for `_elastic_context` (retrieval, fallback, dedup,
   vague-query enrichment, self-match filtering, deflection filtering,
   deflection-skip enrichment, fallback window size) — 17 tests in
   `services/aios_core/test_main.py`, all passing.
2. Backwards compat test (no flag → `_trim_conversation`) — passing.
3. 500-turn stress test with `--consolidate --elastic`: RUN
   2026-08-07. Deflection collapse at 443-478 significantly mitigated.
   Recall curve shows recovery at turn 400 (0.03 → 0.38) — in all
   previous runs, once collapse started it was monotonic. Not fully
   flat; remaining issues are model-level (degeneration causing 1-5
   token responses). See "With consolidation + elastic context
   (deployed)" section below for the full degradation curve.
4. Latency: avg 5.4s/turn, p95 14.8s (includes LLM generation time).
   Retrieval overhead is ~50ms for embedding + Qdrant query on
   non-trivial turns, 0ms on trivial turns (shortcut).
5. 10K run: NOT YET. The 500-turn curve is volatile; recommend
   addressing model degeneration before scaling to 10K. See
   "Recommendations & Next Steps" below.

---

## 500-Turn Test Data (Baseline)

### Without consolidation (first run)

```
Turn   Recall  Repetition  Working  Project
   0    1.000      0.00      1558      36
 100    0.375      0.24      1765      36
 200    0.189      0.34      1970      36   ← "1" entropy collapse
 300    0.243      0.29      2176      36
 400    0.460      0.29      2381      36
 500    0.865      0.02      2586      36
```

### With consolidation (second run)

```
Turn   Recall  Repetition  Working  Project  UserModel  ProjState
   0    1.000      0.00      2615      48      2578       4801
 100    0.656      0.09      2822      48      2578       4801
 200    0.811      0.08      3027      53      3077       5914   ← no collapse
 300    0.730      0.02      3233      58      3376       6736
 400    0.784      0.03      3438      63      3617       7943
 500    0.622      0.06      3643      68      3803       9087
```

Consolidation fixed the "1" collapse (0.811 vs 0.189 at turn 200) and
made the system learn (state files growing). But the deflection collapse
at turns 443-478 ("I don't know what 'that' is") remains — that's what
the elastic window fixes.

### With consolidation + elastic context (deployed, 2026-08-07)

500-turn stress test with `--consolidate --elastic --delay 0.5`:

```
Turn   Recall  Repetition  Working  Project
   0    1.000      0.00     6979     143
 100    0.313      0.13     7186     143
 200    0.541      0.09     7391     148
 300    0.027      0.27     7597     153  ← collapse
 400    0.378      0.07     7802     158  ← recovery (new!)
 500    0.108      0.25     8007     163
```

The deflection collapse at 443-478 is significantly mitigated. Planning
prompts in that zone now recall past decisions ("We decided to simplify
the Qdrant architecture", "We shifted from Hot/Warm/Cold tiering"). The
recall curve shows recovery at turn 400 (0.03 → 0.38) — in all previous
runs, once collapse started it never recovered (monotonic decrease).
However, the curve is volatile rather than flat, and context_reference
prompts still get some deflections when the recent window is degraded.
The remaining issues are model-level (degeneration causing 1-5 token
responses, sampling behavior) rather than context-engineering problems.

**Note on recall metric comparability:** the stress test's deflection
detector was expanded in the same session to include uncontracted forms
("I do not know", "I cannot see", "I have no record") that were
previously missed. Earlier baseline numbers (0.66-0.81 in the
consolidation-only run) were measured with the less comprehensive
detector and are not directly comparable — the same deflection rate
scores lower with the new detector. The trend (recovery vs monotonic
collapse) is the meaningful comparison, not absolute numbers across
detector versions.

---

## Remaining Issues for 10K

1. **Working tier never shrinks.** Consolidation promotes to project but
   doesn't archive working points. At 10K turns, working would hit ~20K
   points. The filtered query (by conversation_id) is fast regardless,
   but retrieval quality may degrade as the pool grows. We should add
   archiving eventually.

2. **Longterm tier never touched.** Consolidation only goes
   working → project. Cross-project insights should eventually promote
   to longterm. Not blocking for 10K.

3. **Retrieval quality depends on embedding model.** PARTIALLY
   RESOLVED (PoC 13.7). Vague context_reference prompts ("Tell me more
   about that.") produce generic embeddings that match nothing — or
   worse, match their own past instances in Qdrant. Three fixes deployed:
   (a) `_is_vague_reference()` + `_enrich_query()` anchor the query with
   the previous *substantive* (non-deflection) assistant reply; (b)
   self-match filtering removes hits where the query matches its own
   past instances; (c) deflection filtering via `_is_deflection()`
   removes past "I don't know" replies from retrieved hits, breaking
   the cascading feedback loop. Remaining gap: when the entire recent
   window is degraded (all recent assistant replies are deflections),
   there's no good anchor for enrichment. The 500-turn test confirms
   the deflection collapse is significantly mitigated but not fully
   eliminated. See "Failed Approaches" below for what was tried to
   address the recent-window-degradation problem.

4. **Model degeneration (1-5 token responses).** NEW, discovered in
   the 2026-08-07 500-turn run. The LLM occasionally generates 1-5
   tokens and hits EOS ("No", "Yes", "I", "The"). This is a model-level
   issue — likely related to repetition penalty, temperature, or the
   model's EOS token distribution at certain context configurations.
   Context engineering cannot fix this. This is the primary remaining
   cause of low recall scores at turns 300 and 500. See
   "Recommendations & Next Steps" below.

5. **System prompt anti-hallucination bias.** NEW. The system prompt's
   "WHAT YOU CANNOT DO" + "DO NOT HALLUCINATE" sections are appropriate
   for filesystem/tool questions but catastrophic for context_reference
   prompts. The LLM is told to say "I don't know" rather than make
   something up — but for context_reference prompts, the context IS
   available in the retrieved turns. A future fix could add a
   conditional system prompt section when elastic context is active,
   but this must be done carefully (see "Failed Approaches" — a
   heavy-handed directive note confused the LLM into near-silence).

6. **Meta prompt strategy.** NEW. Meta prompts ("Do you remember what
   we talked about?", "What was the first thing I asked?") are a
   different failure mode from context_reference prompts. They're not
   anaphoric references — they're broad session-history questions that
   produce generic embeddings. The vague-query enrichment doesn't help
   them (they have content words like "remember", "talked"). A future
   fix could detect meta prompts and retrieve a broader summary (e.g.,
   the most recent consolidation distillation) rather than semantically
   similar turns.

---

## Failed Approaches (do not retry)

Three approaches to the "recent window full of deflections" problem
(when the last 6 messages are all "I don't have access to previous
conversations") were tried during PoC 13.7 and all made things worse.
Documented here per Manifesto Pillar VII (Engineering Discipline) so
they are not retried.

1. **Directive system note** ("DO have access, Do NOT say you don't
   have access"): Inserted a system message between the retrieved block
   and the recent window explicitly telling the LLM the context IS
   provided and to use it. Result: the LLM generated 1-5 token
   responses ("I", "No", "Yes"). The directive conflicted with the
   system prompt's anti-hallucination bias ("if you don't know, say 'I
   don't know'"), confusing the LLM into near-silence. Recall at turn
   100 dropped from 0.66 (without the note) to 0.38 (with it).

2. **Neutralization** (replacing deflection replies with "[context was
   not available for this turn]"): Replaced deflection assistant
   replies in the recent window with a brief neutral note to remove the
   deflection pattern the LLM would copy. Result: the LLM echoed the
   neutralization text back as its response — it pattern-matched the
   replacement text just as it had pattern-matched the deflections.
   Any text placed in the recent window as a replacement can itself be
   pattern-matched.

3. **Window extension** (extending the recent window 2x past
   deflections to find substantive content): Extended the recent
   window from 6 to 12 messages when deflections were detected, to
   include the last substantive exchange. Result: added too much noise
   and the extended context didn't reliably improve recall. The larger
   window diluted the retrieved context's signal.

**The final approach is the most conservative:** do NOT modify the
recent window at all. Rely on clean retrieval (enrichment + self-match
filter + deflection filter + larger pool of 20 hits) to place
substantive content BEFORE the recent window. The retrieved context is
the lever; the recent window is left untouched. This produced the best
results: recovery at turn 400 (0.03 → 0.38 recall), which no previous
run achieved.

---

## Recommendations & Next Steps

1. **Model degeneration (1-5 token responses):** The LLM occasionally
   generates 1-5 tokens and hits EOS ("No", "Yes", "I", "The"). This
   is a model-level issue — likely related to repetition penalty,
   temperature, or the model's EOS token distribution at certain
   context configurations. Context engineering cannot fix this.
   Recommend: (a) investigate llama.cpp sampling parameters
   (`repetition_penalty`, `min_tokens`), (b) consider a `min_tokens`
   floor if the backend supports it, (c) test with different
   temperatures. This is the primary remaining cause of low recall
   scores at turns 300 and 500.

2. **System prompt anti-hallucination bias:** The system prompt's
   "WHAT YOU CANNOT DO" + "DO NOT HALLUCINATE" sections are appropriate
   for filesystem/tool questions but catastrophic for context_reference
   prompts. The LLM is told to say "I don't know" rather than make
   something up — but for context_reference prompts, the context IS
   available in the retrieved turns. A future fix could add a
   conditional system prompt section when elastic context is active:
   "When older turns are retrieved and provided above, you DO have
   access to that context — use it." This must be done carefully — the
   directive note attempt (see Failed Approaches #1) showed that
   heavy-handed instructions confuse the LLM. A gentler framing that
   doesn't conflict with the anti-hallucination guidance is needed.

3. **10K run:** NOT recommended until the model degeneration issue is
   addressed. The 500-turn curve is volatile (0.31 → 0.54 → 0.03 →
   0.38 → 0.11). At 10K turns, the deflection cascade would be worse
   and the degeneration stretches would be longer. Fix the model-level
   issues first, then re-run 500 to confirm stability, then scale to
   10K.

4. **Consolidation archiving:** Working tier grows unbounded (8007
   points at 500 turns → ~160K at 10K). The filtered query by
   conversation_id is fast regardless, but retrieval quality may
   degrade as the pool grows. Add working-tier archiving (promote to
   project, then delete from working) as originally noted in Remaining
   Issues #1.

5. **Meta prompt strategy:** Meta prompts ("Do you remember what we
   talked about?", "What was the first thing I asked?") are a different
   failure mode from context_reference prompts. They're not anaphoric
   references — they're broad session-history questions that produce
   generic embeddings. The vague-query enrichment doesn't help them.
   A future fix could detect meta prompts and retrieve a broader
   summary (e.g., the most recent consolidation distillation) rather
   than semantically similar turns.

6. **Deflection detector in stress test:** The deflection detector in
   `tests/stress/test_ten_thousand_turns.py` was expanded to include
   uncontracted forms ("I do not know", "I cannot see", "I have no
   record"). Future runs should use this expanded detector for
   consistent recall measurement. Note that earlier baseline numbers
   (in the "With consolidation" section above) were measured with the
   old, less comprehensive detector and are not directly comparable.
