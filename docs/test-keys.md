# Test keys and provider docs (web app agent)

*Last updated by Abdul, Oct 17 2026: A0 first version (docs read on Oct 17 2026 by the Builder).*

No key value is written in this file or anywhere in the repo. Keys live only in environment variables or the git-ignored `.env`.

## Official pages read (Oct 17 2026)

| Provider | Pages |
|---|---|
| Gemini | https://ai.google.dev/gemini-api/docs/quickstart , https://ai.google.dev/gemini-api/docs/function-calling , https://ai.google.dev/gemini-api/docs/pricing , https://ai.google.dev/gemini-api/docs/rate-limits , https://ai.google.dev/gemini-api/docs/migrate-to-interactions (full page read by the Reviewer, Oct 17) |
| OpenAI | https://developers.openai.com/api/docs/guides/function-calling (the old platform.openai.com URL redirects here) |
| Anthropic | https://platform.claude.com/docs/en/agents-and-tools/tool-use/overview , https://platform.claude.com/docs/en/models/overview |

## Packages and default models

| Provider | `uv add` package (version installed) | Default model id | Tool-calling API the docs show |
|---|---|---|---|
| Gemini | `google-genai` (2.31.0; the Gemini docs do not name the pip package, verified only by installing it and importing); `from google import genai`; `genai.Client()` | `gemini-3.8-flash` (the id used in the quickstart and function-calling pages, and listed "Free of charge" for input and output on the pricing page, Standard Free Tier) | The function-calling page (read Oct 17) shows only the **Interactions API**: `client.interactions.create(model=..., input=..., tools=[{"type":"function","name":...,"parameters":{...}}])`, a `function_call` step with `.name`, `.arguments`, `.id`, and a `function_result` input with `call_id` plus `previous_interaction_id`. The full migration page (read Oct 17) says `generateContent` "remains fully supported" with no deprecation date given, and recommends the **Interactions API for all new development**. Decision for A1: default to Interactions and cite that page. |
| OpenAI | `openai` (3.33.0); `from openai import OpenAI` | `gpt-6-astra` (the id used in the function-calling guide examples). Unknown: whether it is the cheapest sensible default; A1 may choose a smaller id only if the docs name one. | **Responses API** (`client.responses.create(model, input, tools)`); tools are dicts `{"type":"function","name","description","parameters"}`; tool calls are `function_call` items in `response.output`; reply with `{"type":"function_call_output","call_id","output"}` items. The guide says some models require the Responses API; Chat Completions is shown with another model. |
| Anthropic | `anthropic` (1.15.0); `anthropic.Anthropic()` | `claude-sonnet-5-5` (confirmed on the models overview: "Claude API ID" row; $2 / $10 per MTok input/output). Cheaper option: `claude-haiku-5-5` (confirmed; from $0.10 / $0.50). | `client.messages.create(model, max_tokens, tools=[{"name","description","input_schema"}], messages)`; reply has `tool_use` blocks (`stop_reason: "tool_use"`); send back a user message with `tool_result` blocks (`tool_use_id`, `content`). |

## Gemini free-tier limits (as documented)

- The rate-limits page (read Oct 17) names the limit types "Requests per minute (RPM)", "Tokens per minute (input) (TPM)" and "Requests per day (RPD)", says the active limits are viewable in AI Studio, and says the Free tier needs an "Active project" or a free trial. It gives **no free-tier numbers**.
- The AI Studio rate-limit page (https://aistudio.google.com/rate-limit) needs a Google login, so the numbers were **not read**. **Free-tier RPM/TPM/RPD: unknown.** Abdul: open that page while logged in and paste the numbers for `gemini-3.8-flash` here with the date.
- Rule for tests: stop on the first 429; do not retry in a loop.

## Creating temporary spend-limited test keys (Claude and OpenAI)

Abdul does these steps in each console himself; the agent never creates keys.

- [ ] **Claude:** open https://platform.claude.com/ , create a new workspace or project named `tietrace-test`, set a low monthly spend limit there, create an API key with the name `tietrace-test-oct17`, copy it once.
- [ ] **OpenAI:** open https://platform.openai.com/ , create a project `tietrace-test`, set a low monthly budget on the project, create a project API key, copy it once.
- [ ] Put each key only in an environment variable for the shell session, never in a file:
  - PowerShell: `$env:TEST_KEY_CLAUDE = "<paste>"` and `$env:TEST_KEY_OPENAI = "<paste>"`
  - Gemini uses `GEMINI_API_KEY` from the git-ignored `.env`.
- [ ] Tests that need a key skip with "not run: no key" when the variable is unset.
- [ ] Close the shell afterwards (the variables disappear).

## Revoke checklist (tick with the date after the live tests)

- [ ] Claude key `tietrace-test-oct17` deleted in the console (date: ____ by: ____)
- [ ] OpenAI project key deleted in the console (date: ____ by: ____)
- [ ] Gemini key: free-tier key in `.env`; revoke or rotate in AI Studio after the demo if wished (date: ____ by: ____)
