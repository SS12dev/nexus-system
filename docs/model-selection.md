# Model selection

Reviewed 2026-11-30 against provider documentation. Prices below are USD per million
tokens and exclude caching, tools, and taxes. Provider pricing and model IDs change; recheck
the linked pages before deployment.

## Current setup

Nexus calls LiteLLM through `ChatOpenAI` and its OpenAI-compatible Chat Completions endpoint.
The four `nexus-*` aliases are configured in `infra/litellm/config.yaml`; the current
OpenAI GPT-5.4/5.5 aliases have not been changed. This keeps model choices out of application
code, but does not make every provider model interchangeable: streaming, tool calls, image
messages, reasoning parameters, and structured output must all work through this endpoint and
our LangChain integration.

## Candidates

| Model | Relevant official capabilities | Published input / output price | Assessment for Nexus |
| --- | --- | --- | --- |
| OpenAI GPT-6 Luna | Text and image input; Chat Completions and function calling, but Chat Completions function calling requires `reasoning_effort=none`; 1.05M context | $0.10 / $0.50 | Strong candidate for routing, extraction, and other small non-tool tasks. Do not use as a tool-using agent without setting and testing the required reasoning effort. |
| Google Gemini 3.8 Flash | Positioned for software engineering and autonomous agents; function calling, image input, structured output, and 1,048,576 input tokens | $0.75 / $3.75 through 2026-12-31; $1.50 / $7.50 from 2027-01-01 | Best low-cost agent candidate to benchmark first. Use a paid tier for user data: Google's free tier may use submitted content to improve products, while its paid tier says it does not. |
| OpenAI GPT-6.1 Sol | Text and image input; Chat Completions endpoint listed, but tool calling requires the Responses API; 1.05M context | $2 / $10 | Capable candidate for complex work, but its documented tool API does not match Nexus's current Chat Completions client. Requires an endpoint/integration change before agent use. |
| Anthropic Claude Sonnet 5.5 | Text and image input, tool use, 1M context, 128K max output; described as a speed/intelligence balance | $2 / $10 | Worth evaluating for agentic coding and planning. Same headline price as GPT-6.1 Sol, with a provider-specific adapter and tool behavior to validate. |
| Anthropic Claude Opus 5.5 | Text and image input, tool use, 1M context, 128K max output; aimed at long-running agentic work | $4 / $20 | Reserve for an escalation tier only if measured quality gains justify twice Sonnet's listed rates. |
| OpenAI GPT-6 Astra | Text and image input; documented function calling and streaming; 1.05M context | $10 / $50 | Premium escalation candidate. Its price is five times GPT-6.1 Sol; do not make it a default without clear evaluation wins. |

Prices are not directly comparable when provider tokenization, cached input, reasoning tokens,
or provider-specific billing differs. Gemini's published price change is scheduled for
2027-01-01; verify it before rollout.

## Recommendation

Do not change the model defaults based on marketing claims or price alone. First run a small,
versioned evaluation set through the same LiteLLM and Chat Completions path Nexus uses. Include
representative Nexus tasks: clarification behavior, tool selection and successful tool
execution, agent-system plans, code/app generation, image/screenshot input, structured output,
and streaming. Score task success and safety first, then measure latency and cost per successful
task. Keep a baseline against the current aliases and test fallback behavior.

For the first comparison, test Gemini 3.8 Flash against `nexus-mini` on paid API access, and
test GPT-6 Luna against `nexus-nano` for non-tool tasks. Test Claude Sonnet only after the
LiteLLM/Chat Completions tool-call path is proven. Test GPT-6.1 Sol only if Nexus adds a
Responses API integration; its current documented tool-call requirement makes it a poor
drop-in replacement.

Do not route user traffic to a candidate until the evaluation passes for quality, tools,
streaming, image input, failure handling, and observed cost. Keep the current aliases as the
rollback path.

## Official sources

- [OpenAI GPT-6 Luna](https://developers.openai.com/api/docs/models/gpt-6-luna)
- [OpenAI GPT-6.1 Sol](https://developers.openai.com/api/docs/models/gpt-6.1-sol)
- [OpenAI GPT-6 Astra](https://developers.openai.com/api/docs/models/gpt-6-astra)
- [Google Gemini 3.8 Flash](https://ai.google.dev/gemini-api/docs/models/gemini-3.8-flash)
- [Google Gemini API pricing](https://ai.google.dev/gemini-api/docs/pricing)
- [Anthropic models overview](https://platform.claude.com/docs/en/models/overview)
- [Anthropic pricing](https://platform.claude.com/docs/en/about-claude/pricing)
