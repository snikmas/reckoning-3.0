# Reckoning finished-product proposal

Status: discussion draft, 2026-09-06. This describes a proposed finished experience. It does not replace the accepted product specification or claim that these behaviors are implemented.

## The product promise

Reckoning is a personal assistant that learns your circumstances, helps you choose what matters, keeps track of commitments, and carries out delegated work. It brings decisions back to you when timing, evidence, or competing priorities change.

You can bring it an unfamiliar request in ordinary language. It works out what it can do, what information it lacks, and which decisions need you. Its capability limits remain visible.

The intended feeling is: "Someone is keeping track with me. I can return without explaining my life again, and I can trust that important unfinished work has a next step."

The full product combines personal understanding, planning, monitoring, research, action, reflection, and a consistent persona. These capabilities belong to one assistant and share the same confirmed context.

## Ongoing responsibilities connect the features

The central interaction is giving Reckoning a responsibility, such as "Help me get these qualifications before my deadline" or "Keep track of this application until I receive a decision."

A responsibility gives the assistant a reason to use memory, consult sources, plan, follow up, and act. It answers what outcome matters, which changes to watch, and when to involve the user. The assistant proposes these details through conversation rather than requiring a long setup form.

Each responsibility has an outcome or maintenance condition, current status, next action, next review, relevant sources, permission limits, and notification preferences. Some responsibilities end at an outcome. Others continue, such as preparing a weekly overview.

The assistant distinguishes three situations:

- An idea is something you are considering. It does not create work or reminders.
- A goal is something you want. The assistant can help clarify and plan it.
- An accepted responsibility authorizes the assistant to keep track within an agreed scope. It does not grant unrestricted external actions.

One-time requests remain welcome. Conversation does not need to become a project, and reflection does not need to produce a task.

The assistant can propose new goals when an expressed ambition, an opportunity, or a missing prerequisite suggests one. Each proposal explains why it may help and what it would displace. Creating a proposal is useful initiative. Turning it into an active commitment remains the user's decision.

## Personal understanding grows through cooperation

### About you

The user profile describes the person. The assistant persona describes the assistant. The product uses different names and controls for them.

A user can import an About me file, answer a short guided interview, or begin with a real situation. The assistant preserves the original wording, proposes a useful summary, and identifies important gaps. Users can correct the summary and exclude sections from active use without rewriting their file.

The suggested About me template asks:

- What should I call you, and which languages do you prefer?
- What is happening in your life now that affects your decisions?
- Which commitments, dates, or constraints must I account for?
- What do you want to achieve, and why does it matter to you?
- What is your current starting point? Which parts are uncertain?
- What kinds of help do you want to delegate?
- How should I challenge you, and what kind of response is unhelpful?
- What should I keep private, avoid inferring, or leave alone?

Every question is skippable. The assistant asks missing questions when their answers could change a recommendation. There is no requirement to produce a complete autobiography before receiving help.

### What the assistant knows

The working understanding distinguishes explicit statements, imported observations, and tentative interpretations. Information has a source and a date. A calendar entry is evidence of a scheduled event, not evidence that the event happened. An empty calendar does not establish free time.

The assistant can explain a recommendation through relevant facts and sources. That explanation identifies the evidence used; it does not claim to expose a model's internal reasoning.

Contradictions prompt clarification when they matter. The assistant does not resolve them by choosing whichever source produces the easiest plan. Direct corrections update current understanding, and old context cannot silently restore the previous claim.

Understanding also grows from outcomes: which estimates were wrong, which suggestions helped, and which interruptions were unwelcome. Observed behavior can justify an adjustment without becoming a permanent personality label.

### Connected sources

Connectors provide useful evidence and actions. They are optional, purpose-specific, and revocable. The product recommends a connector by explaining its benefit, such as "Your calendar would help me avoid proposing study sessions during classes."

Calendar, task, document, mail, and project sources can support different responsibilities. A connection shows what it can read, what it can change, its last successful synchronization, and what work depends on it. The finished product needs complete supported integrations, not a promise to support every service.

More relevant, current evidence can improve understanding. More connected accounts alone do not establish better understanding. Unrelated data adds noise, cost, and correction work.

Source access and permission to send content to a model provider remain separate. Local storage does not imply that cloud model processing stays on the device.

## Goals become plans with review points

For an important goal, Reckoning establishes the desired outcome, purpose, deadline, baseline, requirements, resources, competing commitments, and important unknowns.

It works backward from the date the outcome is actually needed. For an exam goal, that can include preparation, assessment, booking, result delivery, and contingency time. These constraints must come from relevant current sources or clearly marked assumptions. This proposal makes no claim about current exam policies.

Forecasts show a recommended start window, a latest plausible start under stated assumptions, effort ranges, and the next point when evidence could improve the estimate. A latest plausible start is not a guarantee of success.

When evidence is weak, the next step may be a diagnostic or a short trial at a realistic pace. The assistant must be able to recommend that step without inventing a precise completion date.

Plans consider goals together. A new commitment shows what it would displace. The assistant recommends a concrete tradeoff, including delaying, reducing, or abandoning work when appropriate. The user keeps the final choice.

The product distinguishes possible time from sustainable capacity. Rest, relationships, unstructured time, and existing obligations can be explicit constraints. Available hours are not automatically hours to fill.

### A hypothetical qualification journey

A user wants IELTS and HSK 5 before graduation. The assistant asks for the IELTS target, intended use, actual required dates, recent level evidence, and a realistic study budget. It asks about competing commitments only where the answer is missing or outdated.

After checking relevant official sources, the assistant proposes a sequence. It explains why one goal needs an earlier start and what assumptions support that recommendation. The user can accept an ongoing responsibility to track both goals.

At the agreed review time, the assistant might say:

> Your planned start window is approaching. The estimate still depends on your current writing level. I suggest an assessment next week, then we can decide whether to start the full plan next month.

If the user adds another commitment, it explains the collision and presents options. If an official requirement changes, it checks whether the change applies to the user's exam, location, intended use, and dates before notifying them.

The assistant then helps with execution: compare preparation resources, propose calendar blocks, prepare a booking checklist, or draft a question to a test center. Sending, booking, or paying follows the user's explicit authorization or an applicable standing permission.

Progress updates revise the forecast. Completion includes checking that the achieved result satisfies the intended purpose, and closing related watches where appropriate.

## Initiative has a purpose and a limit

Accepted responsibilities can authorize periodic review of goal timing, dependencies, progress, and selected external requirements. The user does not need to remember every future question.

An interruption names what changed, why it matters now, the evidence, and a suggested next action. Repeated information is grouped or suppressed. General news and low-value discoveries belong in an optional digest.

Rumors about an exam becoming harder are not confirmed policy changes. Material claims require source verification and an applicability check. The assistant records the last successful check and the next scheduled check. A failed check is never represented as no change.

Notifications distinguish urgent decisions, ordinary reviews, and optional information. Users can change cadence, quiet hours, and follow-up intensity through conversation. Silence after a reminder does not imply consent, refusal, failure, or a character flaw.

The assistant can suggest a responsibility that appears useful, but monitoring begins only when authorized. Broad personal understanding is not blanket permission to watch every part of someone's life.

## Delegated work produces usable results

Reckoning supports research, comparisons, summaries, draft documents, preparation checklists, scheduling proposals, and actions available through approved tools. The same assistant can handle study, projects, applications, and everyday administration.

It can accept an outcome such as "Prepare what I need for this application" and identify the necessary steps. It brings back a result, an approval request for a prepared action, or a precise blocker. It does not return a long plan and leave all the work to the user.

For recurring tasks, the assistant remembers the agreed method and permission limits. A completed action has verifiable execution evidence. An unavailable connector produces an explicit handoff with the information needed to finish manually.

### A job-search responsibility

For "Help me find suitable paid work," the assistant clarifies role types, demonstrated skills, location or remote constraints, eligibility, available hours, and compensation requirements. It agrees on sources and a useful review cadence.

It monitors supported sources, removes duplicates, checks whether listings remain open, and presents a selective shortlist with evidence of fit, gaps, deadlines, and uncertainties. It can propose a preparation goal when several suitable roles reveal the same missing requirement.

It prepares tailored application materials using verified experience, keeps application status, and proposes follow-ups. It does not invent qualifications or submit applications without authorization. The product promises research, preparation, and reliable tracking; it cannot promise a job offer.

Source coverage is explicit. A failed or inaccessible job source cannot become a claim that there are no suitable openings. User feedback about rejected recommendations improves the next shortlist.

## Persona makes cooperation consistent

Simon has a stable voice, standards, humor, and way of challenging reasoning. Users can select or author a persona and adjust directness, warmth, brevity, and handling of difficult moments.

The assistant adapts its support to expressed preferences and observed needs. If a user wants help following through, Simon can maintain agreed check-ins and prepare smaller next steps. A persona description alone does not provide those capabilities.

Personality cannot justify invented knowledge, personal attacks, manipulation, or overriding a decision. The assistant can disagree with a conclusion while accepting the user's correction of their intended meaning.

Changing model providers preserves the profile, confirmed decisions, responsibilities, and persona configuration. Consistency still needs evaluation because models may express the same configuration differently.

## The product teaches cooperation while helping

First use starts with a real situation. The assistant demonstrates how to provide context, delegate a responsibility, correct a misunderstanding, and set a follow-up. A short guide remains available when needed.

The guide shows usable requests:

- "Help me work out what I should focus on this month."
- "Keep track of this goal and tell me when I need to act."
- "Compare these options using my budget and current commitments."
- "Draft this, but ask before sending it."
- "That was an idea, not a commitment."
- "My circumstances changed. Help me revise the plan."
- "Show me what you are responsible for and what needs me."
- "I have been away. Help me resume."

The assistant fills in missing delegation details with sensible proposals. Users do not need to know prompt engineering or describe every workflow step.

## Everyday use remains understandable

Home shows what needs attention, what changed, what the assistant is handling, and any blocked responsibility. It also explains when nothing needs attention.

Conversation supports reasoning and delegation. Plan shows goals, timing, dependencies, and competing commitments. Review connects previous decisions to outcomes. Control contains data, sources, permissions, budgets, failures, and recovery.

Telegram provides remote conversation and useful delivery while sharing confirmed state with the web application. A responsibility needs a running execution environment and a working delivery channel to notify the user. Local-only operation reports when the machine's availability limits monitoring.

After an absence, the assistant helps the user decide what still matters and presents a manageable restart. It does not demand completion of accumulated check-ins before allowing progress.

## Affordable intelligence is part of the product

The proposed routing policy uses ordinary code for schedules, date arithmetic, synchronization, permissions, and budget enforcement. Models interpret information and make proposals. They do not decide whether they have permission to act.

An inexpensive model can be evaluated for bounded extraction, classification, short summaries, and routine conversation. A more capable model can be evaluated for conflicting evidence, complex planning, important source interpretation, and difficult personal conversations.

The boundary is based on measured task performance. Research is not automatically easy because it begins with search. Missing a qualification in an official requirement can invalidate a plan. A model's own confidence is insufficient to decide that its output is safe.

OpenAI's [model-selection guidance](https://developers.openai.com/api/docs/guides/model-selection) recommends defining a quality target, measuring performance, and then testing cheaper models while preserving that quality. Reckoning should compare candidate routes on multilingual corrections, source accuracy, planning conflicts, persona consistency, and user correction effort.

The preferred personal operating budget is US$5 to US$10 per month for model calls and connected tools. This is a design target, not a verified operating cost. Useful work takes priority over minimizing the bill, and higher-cost configurations should remain available with an explicit budget change.

The assistant supports a user-selected monthly budget, per-run limits, and approved providers. Optional background work is reduced before consuming the allowance needed for expected interactions. If adequate quality is unavailable within the budget, the task is deferred or returned with an explicit limitation. An important task does not automatically authorize extra spending or a new provider.

Cost estimates include input context, output, retries, tool calls, and hosting where applicable. Long history is retrieved selectively. Unchanged sources do not trigger repeated full analysis. Prices and capabilities are checked before selecting providers. DeepSeek's [current pricing page](https://api-docs.deepseek.com/quick_start/pricing/) illustrates why input, output, caching, and execution timing all affect a bill. No monthly affordability claim has been established for this product.

### Candidate cost configurations

The following are configurations to evaluate, not measured quality rankings or subscription offers. Prices were checked on 2026-09-06. All figures are US dollars per one million tokens, listed as input then output. OpenAI figures use Standard processing and short context with uncached input. DeepSeek figures use peak pricing with uncached input.

- Economical candidates: DeepSeek V4 Flash costs $0.44 input and $1.32 output. GPT-5.6 Luna costs $0.20 input and $1.20 output. Either needs evaluation on the actual tasks before becoming the everyday model.
- Intermediate candidates: DeepSeek V4 Pro costs $1.32 input and $3.96 output. GPT-5.6 Terra costs $2 input and $12 output. Consider them for tasks where a cheaper route fails the required quality.
- Higher-cost candidates: GPT-5.6 Sol costs $4 input and $20 output. GPT-6 Astra costs $10 input and $50 output. Reserve these for demonstrated quality gains on difficult work. Sol's listed rate is promotional.

Sources: [DeepSeek pricing](https://api-docs.deepseek.com/quick_start/pricing/) and [OpenAI pricing](https://developers.openai.com/api/docs/pricing). Regional access, account access, context size, and provider routing can change the available choices and bill. No provider account was tested for this proposal.

For an illustrative monthly workload of two million uncached input tokens and 300,000 billable output tokens across all calls, V4 Flash costs $1.28, V4 Pro costs $3.83, and Astra costs $35. These are calculations from the rates above, not usage forecasts. Billable output must include any charged reasoning tokens. Search, hosting, and other tool costs are additional.

An illustrative routing mix sends 80% of those input and output tokens to V4 Flash and 20% to V4 Pro. Model cost is about $1.79. At OpenAI's listed web-search call fee of $10 per 1,000 calls, 150 calls add $1.50 before search-content token charges. This search example illustrates a separate expense; it is not a claim that this tool integrates directly with DeepSeek. The search provider must be selected and budgeted with the implementation.

The US$5 to US$10 target therefore deserves testing with selective monitoring, an economical default, and bounded escalation. A US$15 to US$30 allowance could fund more capable-model use or more research. US$40 and above could support substantially more premium-model work at the illustrated volume. These allowances buy different amounts of computation and tool use, not guaranteed intelligence or outcomes.

## What makes the finished product complete

The finished product supports the whole qualification journey above through its ordinary interfaces: onboarding, source connection, baseline clarification, forecasting, monitoring, conflict handling, delegated preparation, follow-up, and outcome review.

It also handles a one-time administrative request and an ongoing maintenance responsibility. All of them use the same personal understanding and permission rules.

Users can inspect and correct what the assistant knows, change their priorities, stop responsibilities, revoke connections, export their data, and recover an installation. Failures and costs are visible. None of these controls require knowledge of the internal record structure.

Completion means the promised responsibilities work together and remain usable over repeated real use. Every possible connector, task, and future feature is not a finite completion condition. The supported capabilities must be named and demonstrated.

Development stages are steps toward this finished experience. They do not redefine the destination. Changes to the destination require an explicit product decision based on what the user wants and what actual use shows.

## Relationship to the existing specification

The [accepted specification](product-spec.md) already includes personal context, forecasts, research, routines, connectors, persona, and real-use evaluation. This proposal makes their shared purpose more explicit through ongoing responsibilities and a complete user journey.

The main proposed clarifications are guided About me capture, useful knowledge rather than maximum collection, responsibility-based delegation, applicability checks for monitored changes, cooperation guidance, return after absence, and quality requirements for economical model routing.

The preferred monthly budget is now US$5 to US$10, with useful higher-cost alternatives. The exact supported connector set and notification defaults remain product choices. They do not prevent reviewing the complete experience described here.
