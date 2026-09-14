# Perplexity Web for Home Assistant

Home Assistant custom integration that brings Perplexity Web AI to Home Assistant Assist as a conversation agent.

## Features

- **Native Conversation Agent**: Subclasses `ConversationEntity` for full compatibility with Assist pipelines.
- **Subscription-Aware Models**: Dynamically queries and exposes models matching your Perplexity tier (Free, Pro, Max).
- **Email & OTP Auth**: Native configuration flow supporting Perplexity's passwordless email sign-in.
- **Multi-Turn Context**: Preserves multi-turn conversation history across turns.
- **Incognito Queries**: Keeps Home Assistant interactions separate from personal search history.

## Installation

### Via HACS

1. Open HACS in Home Assistant.
2. Go to **Integrations** > Three dots menu (top right) > **Custom repositories**.
3. Add `https://github.com/veronoicc/perplexity-web-ha` as an **Integration**.
4. Search for "Perplexity Web" and click **Download**.
5. Restart Home Assistant.

### Manual

Copy `custom_components/perplexity_web` into your Home Assistant `<config_dir>/custom_components/` directory and restart.

## Configuration

1. In Home Assistant, go to **Settings** > **Devices & Services** > **Add Integration**.
2. Search for **Perplexity Web**.
3. Enter your account email to receive a one-time verification code.
4. Enter the verification code.
5. In integration **Options**, configure preferred model, search focus (Internet, Scholar, Writing, Wolfram, YouTube, Reddit), and reasoning variant.
6. Set the Perplexity agent in **Settings** > **Voice Assistants** > **Assist**.
